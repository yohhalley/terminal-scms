"""D-047: cancelling or excepting a discharge order frees its reserved yard slot.

Stacked on TCL-012 (#38): the reservation lives on ``Position.is_reserved``.
A release happens only while the container has not been dropped in that slot.
"""

import threading

from django.db import connection, connections
from django.test import TransactionTestCase

from minitos_core.models import CarrierVisit, Che, Order, Position
from minitos_core.scenarios import reset_scenario
from minitos_core.services import yard_planning
from minitos_core.services.discharge_planning import (
    CODE_NO_FREE_SLOT,
    DischargePlanningError,
    plan_visit_discharge,
)
from minitos_core.services.order_execution import apply_execution_event
from minitos_core.test_discharge_planning import (
    DischargePlanningTestBase,
    block_slots,
    drop_seeded_orders,
)

LOGGER = 'minitos_core.services.yard_planning'


class SlotReleaseTests(DischargePlanningTestBase):
    def setUp(self):
        super().setUp()
        drop_seeded_orders()
        self.move_to_in_operation()
        self.orders = list(self.discharge_orders())

    def slot_for(self, order):
        slot = order.to_position.stack_position
        slot.refresh_from_db()
        return slot

    def order_on_20ft_slot(self):
        """A planned order whose reserved bay accepts the import_v1 22G1 (20').

        Discharge planning does not filter by bay size, so a reserved slot may
        be an even (40') bay that Position.save will reject on drop.
        """
        for order in self.orders:
            slot = self.slot_for(order)
            if slot.pile.allows_20:
                return order, slot
        self.fail('planning reserved no 20-foot slot; import_v1 containers are 22G1')

    def free_pks(self, slot):
        return [item.pk for item in yard_planning.free_yard_slots(slot.pile.storage_grid)]

    def cancel(self, order):
        return apply_execution_event(order.pk, 'order_cancelled')

    def test_cancel_frees_the_reserved_slot(self):
        order = self.orders[0]
        slot = self.slot_for(order)
        self.assertTrue(slot.is_reserved)
        self.assertIsNone(slot.container_visit_id)

        with self.assertLogs(LOGGER, level='INFO') as captured:
            self.cancel(order)

        slot.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.CANCELLED)
        self.assertFalse(slot.is_reserved)
        self.assertIsNone(slot.container_visit_id)
        self.assertIn(slot.pk, self.free_pks(slot))
        self.assertTrue(
            any(
                'D-047 released reserved yard slot' in line
                and slot.operational_position.code in line
                and str(order.pk) in line
                for line in captured.output
            ),
            captured.output,
        )

    def test_exception_frees_the_reserved_slot(self):
        """A handoff_exception that lands elsewhere frees the planned slot."""
        order = self.orders[0]
        reserved = self.slot_for(order)
        elsewhere = (
            block_slots()
            .exclude(pk=reserved.pk)
            .filter(
                is_reserved=False, container_visit__isnull=True,
                pile__allows_20=True,
            )
            .first()
        )
        self.assertIsNotNone(elsewhere)

        with self.assertLogs(LOGGER, level='INFO') as captured:
            apply_execution_event(
                order.pk, 'handoff_exception',
                details={
                    'reason': 'SIMULATOR_FAILURE',
                    'destination_position_id': elsewhere.pk,
                },
            )

        reserved.refresh_from_db()
        elsewhere.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.COMPLETE)
        self.assertEqual(elsewhere.container_visit_id, order.container_number_id)
        self.assertFalse(reserved.is_reserved)
        self.assertIsNone(reserved.container_visit_id)
        self.assertIn(reserved.pk, self.free_pks(reserved))
        self.assertNotIn(elsewhere.pk, self.free_pks(reserved))
        self.assertTrue(
            any(
                reserved.operational_position.code in line and str(order.pk) in line
                for line in captured.output
            ),
            captured.output,
        )

    def test_cancel_after_the_drop_does_not_free_the_slot(self):
        order, slot = self.order_on_20ft_slot()
        qc = Che.objects.get(name='QC01')
        apply_execution_event(
            order.pk, 'container_picked_up', che_id=qc.pk,
            details={'source': 'VESSEL'},
        )
        apply_execution_event(
            order.pk, 'container_dropped', che_id=qc.pk, position_id=slot.pk,
        )
        slot.refresh_from_db()
        self.assertEqual(slot.container_visit_id, order.container_number_id)
        self.assertTrue(slot.is_occupied)

        self.cancel(order)

        slot.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.CANCELLED)
        self.assertTrue(slot.is_occupied)
        self.assertEqual(slot.container_visit_id, order.container_number_id)
        self.assertTrue(slot.is_reserved)
        self.assertNotIn(slot.pk, self.free_pks(slot))

    def test_cancelling_twice_is_a_noop(self):
        order = self.orders[0]
        slot = self.slot_for(order)
        with self.assertLogs(LOGGER, level='INFO') as first:
            self.cancel(order)
        slot.refresh_from_db()
        self.assertFalse(slot.is_reserved)
        self.assertEqual(
            sum(1 for line in first.output if 'D-047 released reserved yard slot' in line),
            1,
        )

        self.cancel(order)
        slot.refresh_from_db()
        order.refresh_from_db()
        self.assertFalse(slot.is_reserved)
        self.assertIsNone(slot.container_visit_id)
        self.assertEqual(order.status, Order.CANCELLED)
        self.assertIn(slot.pk, self.free_pks(slot))

    def test_freed_slot_is_chosen_again_and_stacking_stays_ground_first(self):
        order = self.orders[0]
        slot = self.slot_for(order)
        grid = slot.pile.storage_grid
        self.assertEqual(slot.tier, 1)
        self.cancel(order)
        slot.refresh_from_db()
        self.assertFalse(slot.is_reserved)

        # Hold every other ground slot so the only free ground slot is the one
        # just released. Higher tiers stay free: choose_slots must still pick
        # ground, not a floating tier-2 stack.
        block_slots().exclude(pk=slot.pk).update(is_reserved=True)
        free = list(yard_planning.free_yard_slots(grid))
        self.assertIn(slot.pk, [item.pk for item in free])
        self.assertTrue(any(item.tier > 1 for item in free))

        planned = plan_visit_discharge(self.visit)
        created = [item for item in planned if item.created]
        self.assertEqual(len(created), 1)
        new_slot = created[0].order.to_position.stack_position
        new_slot.refresh_from_db()
        self.assertEqual(new_slot.pk, slot.pk)
        self.assertEqual(new_slot.tier, 1)
        self.assertTrue(new_slot.is_reserved)

    def test_exception_onto_the_reserved_slot_keeps_it_occupied(self):
        """When the exception move uses the planned slot, do not free it."""
        order, reserved = self.order_on_20ft_slot()
        apply_execution_event(
            order.pk, 'handoff_exception',
            details={
                'reason': 'SIMULATOR_FAILURE',
                'destination_position_id': reserved.pk,
            },
        )
        reserved.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.COMPLETE)
        self.assertEqual(reserved.container_visit_id, order.container_number_id)
        self.assertTrue(reserved.is_occupied)
        self.assertTrue(reserved.is_reserved)
        self.assertNotIn(reserved.pk, self.free_pks(reserved))

    def test_leg_failed_also_frees_the_slot(self):
        order = self.orders[0]
        slot = self.slot_for(order)
        leg = order.order_legs.order_by('sequence').first()
        apply_execution_event(order.pk, 'leg_failed', leg_id=leg.pk)
        slot.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, Order.FAILED)
        self.assertFalse(slot.is_reserved)
        self.assertIn(slot.pk, self.free_pks(slot))

    def test_cancel_after_tt_pickup_frees_the_slot(self):
        """The reserved slot is freed even after the container has left the vessel.

        Mid-leg exception after TT pickup is the same rule: if the container
        was not dropped in the planned slot, the reservation is released
        (the exception path may occupy a different BLOCK01 slot).
        """
        order = self.orders[0]
        slot = self.slot_for(order)
        qc = Che.objects.get(name='QC01')
        tt = Che.objects.get(name='TT01')
        apply_execution_event(
            order.pk, 'container_picked_up', che_id=qc.pk,
            details={'source': 'VESSEL'},
        )
        apply_execution_event(
            order.pk, 'container_transferred',
            details={'from_che_id': qc.pk, 'to_che_id': tt.pk},
        )
        self.cancel(order)

        slot.refresh_from_db()
        order.container_number.refresh_from_db()
        self.assertFalse(slot.is_reserved)
        self.assertIsNone(slot.container_visit_id)
        self.assertIn(slot.pk, self.free_pks(slot))
        self.assertEqual(order.container_number.current_che_id, tt.pk)


class SlotReleasePostgresRaceTests(TransactionTestCase):
    """Cancel vs a concurrent planning run that wants the same slot.

    Skipped unless the database is Postgres: both paths use
    ``select_for_update``, which SQLite cannot interlock the same way.
    Expected: no double-booking, ``is_reserved`` matches open orders,
    no deadlock.
    """

    containers = 2
    race_rounds = 8
    join_timeout_s = 20

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'Postgres-only race: cancel vs planning both use select_for_update'
            )

    def _reset_and_contest_slot(self):
        reset_scenario('import_v1', containers=self.containers)
        drop_seeded_orders()
        visit = CarrierVisit.objects.get()
        visit.status = CarrierVisit.IN_OPERATION
        visit.save()
        visit.refresh_from_db()
        orders = list(
            Order.objects.filter(
                container_number__ib_carrier=visit, order_type=Order.MOVE_IN,
            ).order_by('pk')
        )
        self.assertEqual(len(orders), 2)
        holder, other = orders
        slot = holder.to_position.stack_position
        self.assertIsNotNone(slot)
        self.assertTrue(slot.is_reserved)
        # Remove the other order so planning must place its container.
        # Hold every other BLOCK01 slot so the only slot planning can take
        # after the release is the contested one.
        other.delete()
        Position.objects.filter(
            pile__storage_grid__name='BLOCK01',
        ).exclude(pk=slot.pk).update(is_reserved=True)
        slot.refresh_from_db()
        self.assertTrue(slot.is_reserved)
        self.assertNotIn(
            slot.pk,
            [item.pk for item in yard_planning.free_yard_slots(slot.pile.storage_grid)],
        )
        return visit.pk, holder.pk, slot.pk

    def _run_one_race(self, visit_id, holder_id, slot_id):
        barrier = threading.Barrier(2, timeout=10)
        errors = []
        outcomes = {}

        def cancel_worker():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '8s'")
                    cursor.execute("SET statement_timeout = '15s'")
                barrier.wait()
                apply_execution_event(holder_id, 'order_cancelled')
                outcomes['cancel'] = 'ok'
            except Exception as exc:  # noqa: BLE001 — surface any worker failure, including a deadlock
                errors.append(('cancel', exc))
                outcomes['cancel'] = exc
            finally:
                connections.close_all()

        def plan_worker():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '8s'")
                    cursor.execute("SET statement_timeout = '15s'")
                barrier.wait()
                planned = plan_visit_discharge(CarrierVisit.objects.get(pk=visit_id))
                outcomes['plan'] = planned
            except DischargePlanningError as exc:
                outcomes['plan'] = exc
                if exc.planning_code != CODE_NO_FREE_SLOT:
                    errors.append(('plan', exc))
            except Exception as exc:  # noqa: BLE001 — surface any worker failure, including a deadlock
                errors.append(('plan', exc))
                outcomes['plan'] = exc
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=cancel_worker, name='d047-cancel'),
            threading.Thread(target=plan_worker, name='d047-plan'),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=self.join_timeout_s)
        alive = [thread.name for thread in threads if thread.is_alive()]
        if alive:
            self.fail(f'deadlock or hang: threads still running: {alive}')
        if errors:
            self.fail(f'race errors: {errors!r}')

        connection.ensure_connection()
        slot = Position.objects.select_related('pile', 'pile__storage_grid').get(pk=slot_id)
        holder = Order.objects.get(pk=holder_id)
        self.assertEqual(holder.status, Order.CANCELLED)

        open_ids = set(
            Order.objects.filter(
                to_position__stack_position=slot,
            ).exclude(status__in=yard_planning.CLOSED_ORDER_STATUSES).values_list('pk', flat=True)
        )
        open_ids.update(
            Order.objects.filter(
                order_legs__end_position__stack_position=slot,
            ).exclude(status__in=yard_planning.CLOSED_ORDER_STATUSES).values_list('pk', flat=True)
        )
        self.assertLessEqual(
            len(open_ids), 1,
            f'double-booked slot {slot.pk}: {open_ids}',
        )
        self.assertIsNone(slot.container_visit_id)
        self.assertFalse(slot.is_occupied)

        free_pks = [item.pk for item in yard_planning.free_yard_slots(slot.pile.storage_grid)]
        if open_ids:
            self.assertTrue(slot.is_reserved, 'open order must keep is_reserved set')
            self.assertNotIn(slot.pk, free_pks)
        else:
            self.assertFalse(slot.is_reserved, 'no open order but is_reserved still set')
            self.assertIn(slot.pk, free_pks)

        plan_outcome = outcomes.get('plan')
        if isinstance(plan_outcome, DischargePlanningError):
            return 'no_free_slot'
        created = [item for item in plan_outcome if item.created]
        if created:
            new_slot = created[0].order.to_position.stack_position
            self.assertEqual(new_slot.pk, slot.pk)
            return 'planned'
        return 'adopted'

    def test_cancel_vs_concurrent_planning_no_double_booking(self):
        kinds = []
        for _round in range(self.race_rounds):
            visit_id, holder_id, slot_id = self._reset_and_contest_slot()
            kinds.append(self._run_one_race(visit_id, holder_id, slot_id))
        planned = kinds.count('planned')
        no_free = kinds.count('no_free_slot')
        self.assertEqual(
            len(kinds), self.race_rounds,
            f'expected {self.race_rounds} rounds, got {kinds}',
        )
        self.assertEqual(
            planned + no_free, self.race_rounds,
            f'unexpected outcomes: {kinds}',
        )
        print(
            f'Postgres race outcomes over {self.race_rounds} rounds: '
            f'{planned} planned onto the freed slot, {no_free} no_free_slot',
            flush=True,
        )
