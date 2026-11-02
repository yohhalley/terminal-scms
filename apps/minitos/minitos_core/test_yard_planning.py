"""TCL-012 (#10): random free BLOCK01 slot per discharge order, seeded (D-016).

Planning runs when the vessel visit goes InOperation (TCL-010). The import_v1
seed still pre-creates its orders until TCL-010b, so the tests drop them first
(the state the seed leaves once TCL-010b lands), as test_discharge_planning does.
"""

import random
from types import SimpleNamespace

from django.test import TestCase

from minitos_core.models import (
    CarrierVisit,
    ContainerOwner,
    ContainerUnit,
    ContainerVisit,
    Order,
    Position,
    ScenarioSeed,
)
from minitos_core.scenarios import reset_scenario, run_step
from minitos_core.scenarios.base import canonical_checksum, canonical_dump, container_number
from minitos_core.services import yard_planning
from minitos_core.services.discharge_planning import (
    CODE_NO_FREE_SLOT,
    DischargePlanningError,
    plan_visit_discharge,
)

CONTAINERS = 5


def drop_seeded_orders():
    Order.objects.all().delete()
    Position.objects.filter(is_reserved=True).update(is_reserved=False)


def block_slots():
    return Position.objects.filter(pile__storage_grid__name='BLOCK01', tier=1).order_by('pk')


def planned_slot_codes(seed, containers=CONTAINERS):
    """Reset with ``seed``, plan at InOperation, return the slot codes in order."""
    reset_scenario('import_v1', seed=seed, containers=containers)
    drop_seeded_orders()
    run_step('import_v1', 'visit-in-operation')
    return [
        order.to_position.code
        for order in Order.objects.filter(order_type=Order.MOVE_IN).order_by('container_number_id')
    ]


class SeededSlotTests(TestCase):
    def test_same_seed_gives_the_same_slots(self):
        first = planned_slot_codes(seed=7)
        second = planned_slot_codes(seed=7)

        self.assertEqual(len(first), CONTAINERS)
        self.assertEqual(len(set(first)), CONTAINERS)
        self.assertEqual(first, second)

    def test_different_seed_gives_different_slots(self):
        self.assertNotEqual(planned_slot_codes(seed=1), planned_slot_codes(seed=2))

    def test_reset_stores_the_scenario_seed_and_the_draw_uses_it(self):
        reset_scenario('import_v1', seed=7, containers=CONTAINERS)
        drop_seeded_orders()
        visit = CarrierVisit.objects.get()
        self.assertEqual(list(ScenarioSeed.objects.values_list('scenario', 'seed')), [('import_v1', 7)])
        free = list(yard_planning.free_yard_slots(block_slots().first().pile.storage_grid))
        expected = random.Random(f'TCL-012:7:{visit.pk}').sample(free, CONTAINERS)

        planned = plan_visit_discharge(visit)

        self.assertEqual(
            [item.order.to_position_id for item in planned],
            [slot.operational_position.pk for slot in expected],
        )

    def test_default_reset_seed_is_k7_seed_1(self):
        reset_scenario('import_v1')
        self.assertEqual(yard_planning.scenario_seed(), 1)

    def test_no_stored_seed_falls_back_to_the_default_planning_seed(self):
        reset_scenario('import_v1', seed=7)
        ScenarioSeed.objects.all().delete()
        self.assertEqual(yard_planning.scenario_seed(), yard_planning.DEFAULT_PLANNING_SEED)

    def test_global_random_state_is_neither_used_nor_changed(self):
        random.seed(12345)
        first = planned_slot_codes(seed=3)
        random.seed(99999)
        state = random.getstate()
        second = planned_slot_codes(seed=3)

        self.assertEqual(first, second)
        self.assertEqual(random.getstate(), state)


class FreeSlotTests(TestCase):
    containers = 3

    def setUp(self):
        reset_scenario('import_v1', containers=self.containers)
        self.visit = CarrierVisit.objects.get()
        self.grid = block_slots().first().pile.storage_grid

    def dwell_container(self, serial):
        visit = ContainerVisit.objects.first()
        unit = ContainerUnit.objects.create(
            container_number=container_number('MSCU', serial), iso_type=visit.container_number.iso_type,
            owner=ContainerOwner.objects.get(), is_in_terminal=True,
        )
        return ContainerVisit.objects.create(
            category=ContainerVisit.IMPORT, container_number=unit, location=visit.location,
            status=ContainerVisit.AT_YARD,
        )

    def test_never_picks_an_occupied_reserved_or_already_targeted_slot(self):
        drop_seeded_orders()
        slots = list(block_slots())
        # One slot of each kind that is not free, then exactly enough free ones.
        Position.objects.filter(pk=slots[0].pk).update(container_visit=self.dwell_container(654321), is_occupied=True)
        Position.objects.filter(pk=slots[1].pk).update(is_occupied=True)
        Position.objects.filter(pk=slots[2].pk).update(is_reserved=True)
        # An open order points at slots[3] but its reserved flag was never set.
        Order.objects.create(order_type=Order.HOUSEKEEPING, status=Order.PLANNED,
                             to_position=slots[3].operational_position)
        keep = slots[4:4 + self.containers]
        block_slots().exclude(pk__in=[slot.pk for slot in slots[:4] + keep]).update(is_reserved=True)

        free = list(yard_planning.free_yard_slots(self.grid))
        self.assertEqual([slot.pk for slot in free], [slot.pk for slot in keep])

        planned = plan_visit_discharge(self.visit)

        self.assertEqual(
            sorted(item.order.to_position_id for item in planned),
            sorted(slot.operational_position.pk for slot in keep),
        )

    def test_a_slot_that_only_a_live_leg_ends_on_is_not_free(self):
        # Seeded order: keep only its leg 3 pointing at the slot (no flag,
        # no order target). It is still taken.
        order = Order.objects.order_by('pk').first()
        slot = order.to_position.stack_position
        Position.objects.filter(pk=slot.pk).update(is_reserved=False)
        Order.objects.filter(pk=order.pk).update(to_position=None)

        self.assertNotIn(slot.pk, [item.pk for item in yard_planning.free_yard_slots(self.grid)])

    def test_closed_orders_do_not_hold_their_slot(self):
        order = Order.objects.order_by('pk').first()
        slot = order.to_position.stack_position
        Position.objects.filter(pk=slot.pk).update(is_reserved=False)
        for closed in yard_planning.CLOSED_ORDER_STATUSES:
            Order.objects.filter(pk=order.pk).update(status=closed)
            self.assertIn(slot.pk, [item.pk for item in yard_planning.free_yard_slots(self.grid)], closed)

    def test_upper_tiers_are_not_planned(self):
        self.assertTrue(Position.objects.filter(pile__storage_grid=self.grid, tier__gt=1).exists())
        self.assertEqual({slot.tier for slot in yard_planning.free_yard_slots(self.grid)}, {1})

    def test_full_block01_raises_a_clear_error_and_plans_nothing(self):
        drop_seeded_orders()
        # Full through open orders only (no reserved flags): still full.
        for slot in block_slots():
            Order.objects.create(order_type=Order.HOUSEKEEPING, status=Order.PLANNED,
                                 to_position=slot.operational_position)
        self.visit.status = CarrierVisit.IN_OPERATION

        with self.assertRaises(DischargePlanningError) as caught:
            self.visit.save()

        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.assertIn('BLOCK01 has 0 free ground slot', caught.exception.planning_message)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertFalse(Order.objects.filter(order_type=Order.MOVE_IN).exists())

    def test_choose_slots_refuses_more_than_free(self):
        with self.assertRaises(ValueError):
            yard_planning.choose_slots([1, 2], 3, random.Random(1))


class ImportBlockTests(TestCase):
    @staticmethod
    def lane(name):
        return SimpleNamespace(storage_grid=SimpleNamespace(name=name))

    def test_block01_lane_is_used_even_if_another_block_sorts_first(self):
        lanes = [self.lane('A-BLOCK'), self.lane('BLOCK01'), self.lane('BLOCK02')]
        self.assertEqual(yard_planning.prefer_import_block(lanes), [lanes[1]])

    def test_layout_without_block01_keeps_the_first_block_lane(self):
        lanes = [self.lane('BLOCK02'), self.lane('BLOCK03')]
        self.assertEqual(yard_planning.prefer_import_block(lanes), lanes)


class ResetDeterminismWithPlanningTests(TestCase):
    def test_two_resets_still_give_identical_state(self):
        reset_scenario('import_v1')
        first = canonical_dump()
        reset_scenario('import_v1')
        self.assertEqual(canonical_checksum(first), canonical_checksum(canonical_dump()))

    def test_two_resets_then_planning_give_identical_state(self):
        planned_slot_codes(seed=7)
        first = canonical_dump()
        planned_slot_codes(seed=7)
        second = canonical_dump()

        self.assertEqual(canonical_checksum(first), canonical_checksum(second))
