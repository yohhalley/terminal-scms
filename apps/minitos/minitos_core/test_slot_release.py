"""D-047: cancelling or excepting a discharge order frees its reserved yard slot.

Stacked on TCL-012 (#38): the reservation lives on ``Position.is_reserved``.
A release happens only while the container has not been dropped in that slot.
"""

from minitos_core.models import Che, Order
from minitos_core.services import yard_planning
from minitos_core.services.discharge_planning import plan_visit_discharge
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
