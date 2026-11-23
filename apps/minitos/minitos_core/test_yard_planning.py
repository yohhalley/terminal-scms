"""TCL-012 (#10): random free BLOCK01 slot per discharge order, seeded (D-016).

Planning runs when the vessel visit goes InOperation (TCL-010). Since TCL-010b
the import_v1 seed creates no orders; drop_seeded_orders() is kept as a guard
so each test starts with no orders whatever the seed does.

Yael (5 Nov 2026): keep BLOCK01; fill ground (tier 1) first, then stack on
tier 2+ up to StorageGrid.max_tier with no floating containers.
"""

import random
from types import SimpleNamespace

from django.db import IntegrityError, transaction
from django.test import TestCase

from minitos_core.models import (
    CarrierVisit,
    ContainerOwner,
    ContainerUnit,
    ContainerVisit,
    ISOType,
    OperationalPosition,
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
    discharge_container_visits,
    plan_visit_discharge,
)

CONTAINERS = 5


def drop_seeded_orders():
    Order.objects.all().delete()
    Position.objects.filter(is_reserved=True).update(is_reserved=False)


def ground_slots():
    return Position.objects.filter(pile__storage_grid__name='BLOCK01', tier=1).order_by('pk')


def block_slots():
    """All BLOCK01 slots up to the grid max_tier (alias kept for older call sites)."""
    return ground_slots()


def all_block_slots():
    return Position.objects.filter(pile__storage_grid__name='BLOCK01').order_by('pk')


def planned_slot_codes(seed, containers=CONTAINERS):
    """Reset with ``seed``, plan at InOperation, return the slot codes in order."""
    reset_scenario('import_v1', seed=seed, containers=containers)
    drop_seeded_orders()
    run_step('import_v1', 'visit-in-operation')
    return [
        order.to_position.code
        for order in Order.objects.filter(order_type=Order.MOVE_IN).order_by('container_number_id')
    ]


def hold_slots(slots):
    """Mark slots held via open orders (no reserved flag), so free_yard_slots skips them."""
    for slot in slots:
        Order.objects.create(
            order_type=Order.HOUSEKEEPING, status=Order.PLANNED,
            to_position=slot.operational_position,
        )


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
        free = list(yard_planning.free_yard_slots(ground_slots().first().pile.storage_grid))
        expected = yard_planning.choose_slots(
            free, CONTAINERS, random.Random(f'TCL-012:7:{visit.pk}'),
            containers=list(discharge_container_visits(visit)),
        )

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


class ScenarioSeedSingletonTests(TestCase):
    """BE review N-2 on #38: ScenarioSeed holds at most one row (unique key);
    ``set_scenario_seed`` upserts on it, so resets keep working."""

    def test_reset_twice_keeps_a_single_row_with_the_latest_seed(self):
        reset_scenario('import_v1', seed=7)
        reset_scenario('import_v1', seed=9)
        self.assertEqual(
            list(ScenarioSeed.objects.values_list('key', 'scenario', 'seed')),
            [(ScenarioSeed.SINGLETON_KEY, 'import_v1', 9)],
        )
        self.assertEqual(yard_planning.scenario_seed(), 9)

    def test_set_scenario_seed_upserts_the_existing_row(self):
        first = yard_planning.set_scenario_seed('import_v1', 3)
        second = yard_planning.set_scenario_seed('import_v1', 4)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ScenarioSeed.objects.count(), 1)
        self.assertEqual(yard_planning.scenario_seed(), 4)

    def test_database_refuses_a_second_row(self):
        yard_planning.set_scenario_seed('import_v1', 3)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScenarioSeed.objects.create(scenario='import_v1', seed=5)
        self.assertEqual(ScenarioSeed.objects.count(), 1)

    def test_database_refuses_another_key(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScenarioSeed.objects.create(key='other', scenario='import_v1', seed=5)
        self.assertFalse(ScenarioSeed.objects.exists())


class FreeSlotTests(TestCase):
    containers = 3

    def setUp(self):
        reset_scenario('import_v1', containers=self.containers)
        self.visit = CarrierVisit.objects.get()
        self.grid = ground_slots().first().pile.storage_grid

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
        # import_v1's containers are 20' (22G1): only odd bays fit them (bay rule).
        slots = [slot for slot in ground_slots() if slot.pile.bay % 2 == 1]
        # One slot of each kind that is not free, then exactly enough free ones.
        Position.objects.filter(pk=slots[0].pk).update(container_visit=self.dwell_container(654321), is_occupied=True)
        Position.objects.filter(pk=slots[1].pk).update(is_occupied=True)
        Position.objects.filter(pk=slots[2].pk).update(is_reserved=True)
        # An open order points at slots[3] but its reserved flag was never set.
        Order.objects.create(order_type=Order.HOUSEKEEPING, status=Order.PLANNED,
                             to_position=slots[3].operational_position)
        keep = slots[4:4 + self.containers]
        ground_slots().exclude(pk__in=[slot.pk for slot in slots[:4] + keep]).update(is_reserved=True)
        # Hold every upper-tier slot so the draw stays on the kept ground slots.
        Position.objects.filter(
            pile__storage_grid=self.grid, tier__gt=1,
        ).update(is_reserved=True)

        free = list(yard_planning.free_yard_slots(self.grid))
        self.assertEqual([slot.pk for slot in free], [slot.pk for slot in keep])

        planned = plan_visit_discharge(self.visit)

        self.assertEqual(
            sorted(item.order.to_position_id for item in planned),
            sorted(slot.operational_position.pk for slot in keep),
        )

    def test_a_slot_that_only_a_live_leg_ends_on_is_not_free(self):
        # A planned order: keep only its leg 3 pointing at the slot (no flag,
        # no order target). It is still taken. TCL-010b: the seed no longer
        # pre-creates the order, so plan one here.
        plan_visit_discharge(self.visit)
        order = Order.objects.order_by('pk').first()
        slot = order.to_position.stack_position
        Position.objects.filter(pk=slot.pk).update(is_reserved=False)
        Order.objects.filter(pk=order.pk).update(to_position=None)

        self.assertNotIn(slot.pk, [item.pk for item in yard_planning.free_yard_slots(self.grid)])

    def test_closed_orders_do_not_hold_their_slot(self):
        plan_visit_discharge(self.visit)  # TCL-010b: the seed plans no order
        order = Order.objects.order_by('pk').first()
        slot = order.to_position.stack_position
        Position.objects.filter(pk=slot.pk).update(is_reserved=False)
        for closed in yard_planning.CLOSED_ORDER_STATUSES:
            Order.objects.filter(pk=order.pk).update(status=closed)
            self.assertIn(slot.pk, [item.pk for item in yard_planning.free_yard_slots(self.grid)], closed)

    def test_full_block01_raises_a_clear_error_and_plans_nothing(self):
        drop_seeded_orders()
        # Full through open orders only (no reserved flags), every tier: still full.
        hold_slots(all_block_slots())
        self.visit.status = CarrierVisit.IN_OPERATION

        with self.assertRaises(DischargePlanningError) as caught:
            self.visit.save()

        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.assertIn('BLOCK01 has 0 free slot', caught.exception.planning_message)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertFalse(Order.objects.filter(order_type=Order.MOVE_IN).exists())

    def test_choose_slots_refuses_more_than_free(self):
        with self.assertRaises(ValueError):
            yard_planning.choose_slots([1, 2], 3, random.Random(1))


class StackingTests(TestCase):
    """Yael 5 Nov: fill tier 1 first, then stack; never float; max_tier from grid."""

    containers = 3

    def setUp(self):
        reset_scenario('import_v1', containers=self.containers)
        drop_seeded_orders()
        self.visit = CarrierVisit.objects.get()
        self.grid = ground_slots().first().pile.storage_grid
        self.assertEqual(self.grid.max_tier, 3)

    def test_max_tier_comes_from_storage_grid(self):
        # import_v1 BLOCK01 is the source of truth (max_tier=3); free_yard_slots
        # never returns a slot above it even if a stray higher Position existed.
        self.assertEqual(
            max(slot.tier for slot in yard_planning.free_yard_slots(self.grid)),
            self.grid.max_tier,
        )

    def test_all_ground_slots_full_leads_to_tier_2(self):
        hold_slots(ground_slots())
        free = list(yard_planning.free_yard_slots(self.grid))
        self.assertTrue(free)
        self.assertTrue(all(slot.tier >= 2 for slot in free))

        planned = plan_visit_discharge(self.visit)

        self.assertEqual(len(planned), self.containers)
        tiers = {
            Order.objects.get(pk=item.order.pk).to_position.stack_position.tier
            for item in planned
        }
        self.assertEqual(tiers, {2})

    def test_lowest_tier_first(self):
        free = list(yard_planning.free_yard_slots(self.grid))
        self.assertTrue(any(slot.tier == 1 for slot in free))
        self.assertTrue(any(slot.tier == 2 for slot in free))

        chosen = yard_planning.choose_slots(free, 1, random.Random(0))

        self.assertEqual(chosen[0].tier, 1)

    def test_no_floating_slots(self):
        # Leave every ground slot free: a large draw must still start on tier 1
        # and only stack on a pile after that pile's lower tiers are chosen.
        free = list(yard_planning.free_yard_slots(self.grid))
        ground_count = sum(1 for slot in free if slot.tier == 1)
        need = ground_count + 5
        chosen = yard_planning.choose_slots(free, need, random.Random(11))

        self.assertEqual([slot.tier for slot in chosen[:ground_count]], [1] * ground_count)
        self.assertTrue(all(slot.tier == 2 for slot in chosen[ground_count:]))
        chosen_keys = {(slot.pile_id, slot.tier) for slot in chosen}
        for slot in chosen:
            for lower in range(1, slot.tier):
                self.assertIn(
                    (slot.pile_id, lower), chosen_keys,
                    f'tier {slot.tier} on pile {slot.pile_id} floats above free tier {lower}',
                )

    def test_choose_slots_never_stacks_while_ground_remains_on_that_pile(self):
        # free_yard_slots is the complete unheld set; choose_slots must not
        # reserve tier 2 of a pile whose tier 1 is still free in that set.
        free = list(yard_planning.free_yard_slots(self.grid))
        ground = [slot for slot in free if slot.tier == 1]
        self.assertGreaterEqual(len(ground), 2)
        chosen = yard_planning.choose_slots(free, 2, random.Random(5))
        self.assertEqual({slot.tier for slot in chosen}, {1})
        free_keys = {(slot.pile_id, slot.tier) for slot in free}
        for slot in chosen:
            for lower in range(1, slot.tier):
                self.assertNotIn((slot.pile_id, lower), free_keys)

    def test_fully_stacked_block_raises_the_error(self):
        hold_slots(all_block_slots())
        self.visit.status = CarrierVisit.IN_OPERATION

        with self.assertRaises(DischargePlanningError) as caught:
            self.visit.save()

        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.assertIn('0 free slot', caught.exception.planning_message)

    def test_same_seed_gives_the_same_stacked_plan(self):
        def stacked_plan(seed):
            reset_scenario('import_v1', seed=seed, containers=self.containers)
            drop_seeded_orders()
            hold_slots(ground_slots())
            run_step('import_v1', 'visit-in-operation')
            return [
                order.to_position.code
                for order in Order.objects.filter(order_type=Order.MOVE_IN).order_by('container_number_id')
            ]

        first = stacked_plan(7)
        second = stacked_plan(7)
        self.assertEqual(len(first), self.containers)
        self.assertEqual(first, second)

        # Confirm the plan really stacked (tier 2), not just that codes matched.
        reset_scenario('import_v1', seed=7, containers=self.containers)
        drop_seeded_orders()
        hold_slots(ground_slots())
        run_step('import_v1', 'visit-in-operation')
        tiers = [
            order.to_position.stack_position.tier
            for order in Order.objects.filter(order_type=Order.MOVE_IN)
        ]
        self.assertEqual(set(tiers), {2})


class UnreachableSlotTests(TestCase):
    """TL N1 / BE S-1 on #38: enough free slots, fewer reachable (gappy grid)
    must be ``discharge_no_free_slot``, never a bare ValueError (a 500)."""

    containers = 3

    def setUp(self):
        reset_scenario('import_v1', containers=self.containers)
        drop_seeded_orders()
        self.visit = CarrierVisit.objects.get()

    def make_gappy_block(self):
        # Odd bays only: import_v1's 20' containers fit there (bay rule).
        keep = [slot for slot in ground_slots() if slot.pile.bay % 2 == 1][:self.containers]
        all_block_slots().exclude(pk__in=[slot.pk for slot in keep]).update(is_reserved=True)
        # A corrupt tier (below ground) is still "free" but never reachable by
        # the tier walk, so len(free) == needed while only needed-1 are reachable.
        Position.objects.filter(pk=keep[0].pk).update(tier=0)
        self.assertEqual(len(yard_planning.free_yard_slots(keep[0].pile.storage_grid)), self.containers)

    def test_choose_slots_shortage_is_a_value_error_with_counts(self):
        free = [
            SimpleNamespace(pk=1, pile_id=1, tier=1),
            SimpleNamespace(pk=2, pile_id=2, tier=0),
        ]
        with self.assertRaises(ValueError) as caught:
            yard_planning.choose_slots(free, 2, random.Random(1))
        self.assertIsInstance(caught.exception, yard_planning.SlotShortageError)
        self.assertEqual((caught.exception.needed, caught.exception.available), (2, 1))

    def test_plan_maps_unreachable_slots_to_no_free_slot(self):
        self.make_gappy_block()

        with self.assertRaises(DischargePlanningError) as caught:
            plan_visit_discharge(self.visit)

        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.assertIn('BLOCK01 has 2 free slot(s) reachable', caught.exception.planning_message)
        self.assertFalse(Order.objects.filter(order_type=Order.MOVE_IN).exists())

    def test_in_operation_save_refuses_unreachable_slots_and_stays_arrived(self):
        self.make_gappy_block()
        self.visit.status = CarrierVisit.IN_OPERATION

        with self.assertRaises(DischargePlanningError) as caught:
            self.visit.save()

        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertFalse(Order.objects.filter(order_type=Order.MOVE_IN).exists())

    def test_check_discharge_plannable_uses_the_same_reachability(self):
        from minitos_core.services.discharge_planning import check_discharge_plannable

        self.make_gappy_block()
        with self.assertRaises(DischargePlanningError) as caught:
            check_discharge_plannable(self.visit)
        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)


class BayLengthTests(TestCase):
    """TL + BE (15 Nov): planning must not reserve a slot the RTG drop refuses
    ("20' containers are not allowed in this bay"). Same rule as the drop:
    ``Pile.can_accept_container`` (20' odd bays, 40'/45' even bays)."""

    containers = 5

    def setUp(self):
        reset_scenario('import_v1', containers=self.containers)
        drop_seeded_orders()
        self.visit = CarrierVisit.objects.get()
        self.grid = ground_slots().first().pile.storage_grid

    def make_mixed(self):
        """Containers 2 and 4 become 40' (42G1); the rest stay 20' (22G1)."""
        forty = ISOType.objects.create(iso_type='42G1', length=ISOType.L40, height=ISOType.HNC)
        visits = list(discharge_container_visits(self.visit))
        for container_visit in visits[1::2]:
            ContainerUnit.objects.filter(pk=container_visit.container_number_id).update(iso_type=forty)
        return list(discharge_container_visits(self.visit))

    def test_mixed_20_40_seed_plans_every_container_in_a_bay_that_fits(self):
        visits = self.make_mixed()
        lengths = sorted(cv.container_number.iso_type.length for cv in visits)
        self.assertEqual(lengths, [ISOType.L20] * 3 + [ISOType.L40] * 2)

        planned = plan_visit_discharge(self.visit)

        self.assertEqual(len(planned), self.containers)
        for item in planned:
            container_visit = ContainerVisit.objects.select_related(
                'container_number__iso_type',
            ).get(pk=item.container_visit.pk)
            slot = Order.objects.get(pk=item.order.pk).to_position.stack_position
            ok, reason = slot.pile.can_accept_container(container_visit)
            self.assertTrue(ok, f'{container_visit} -> {slot}: {reason}')
            expected_parity = 1 if container_visit.container_number.iso_type.length == ISOType.L20 else 0
            self.assertEqual(slot.pile.bay % 2, expected_parity, str(slot))

    def test_the_drop_accepts_every_planned_slot(self):
        """Assigning the container to its planned slot runs the drop's checks."""
        self.make_mixed()
        for item in plan_visit_discharge(self.visit):
            slot = Order.objects.get(pk=item.order.pk).to_position.stack_position
            slot.container_visit = ContainerVisit.objects.get(pk=item.container_visit.pk)
            slot.save()  # raises ValidationError if the bay refuses the length

    def test_same_seed_gives_the_same_mixed_plan(self):
        self.make_mixed()
        first = [item.order.to_position_id for item in plan_visit_discharge(self.visit, rng=random.Random(3))]
        Order.objects.all().delete()
        Position.objects.filter(is_reserved=True).update(is_reserved=False)
        second = [item.order.to_position_id for item in plan_visit_discharge(self.visit, rng=random.Random(3))]
        self.assertEqual(first, second)

    def test_no_compatible_free_slot_is_discharge_no_free_slot(self):
        # Every odd (20') bay slot is held; plenty of even (40') bay slots are free.
        hold_slots([slot for slot in all_block_slots() if slot.pile.bay % 2 == 1])
        free = list(yard_planning.free_yard_slots(self.grid))
        self.assertGreaterEqual(len(free), self.containers)
        self.visit.status = CarrierVisit.IN_OPERATION

        with self.assertRaises(DischargePlanningError) as caught:
            self.visit.save()

        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.assertIn('fit the container sizes', caught.exception.planning_message)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertFalse(Order.objects.filter(order_type=Order.MOVE_IN).exists())


    def make_lengths(self, iso_code, length, height=ISOType.HHC):
        """Containers 2 and 4 become ``length`` (``iso_code``); the rest stay 20'."""
        iso_type = ISOType.objects.create(iso_type=iso_code, length=length, height=height)
        visits = list(discharge_container_visits(self.visit))
        for container_visit in visits[1::2]:
            ContainerUnit.objects.filter(pk=container_visit.container_number_id).update(iso_type=iso_type)
        return list(discharge_container_visits(self.visit))

    def test_45_foot_containers_go_to_even_bays_like_40(self):
        """BE review N-1 on #38: 45' uses the even-bay (allows_45) rule too."""
        visits = self.make_lengths('L5G1', ISOType.L45)
        lengths = sorted(cv.container_number.iso_type.length for cv in visits)
        self.assertEqual(lengths, [ISOType.L20] * 3 + [ISOType.L45] * 2)

        planned = plan_visit_discharge(self.visit)

        self.assertEqual(len(planned), self.containers)
        for item in planned:
            container_visit = ContainerVisit.objects.select_related(
                'container_number__iso_type',
            ).get(pk=item.container_visit.pk)
            slot = Order.objects.get(pk=item.order.pk).to_position.stack_position
            ok, reason = slot.pile.can_accept_container(container_visit)
            self.assertTrue(ok, f'{container_visit} -> {slot}: {reason}')
            expected_parity = 1 if container_visit.container_number.iso_type.length == ISOType.L20 else 0
            self.assertEqual(slot.pile.bay % 2, expected_parity, str(slot))
            # The drop accepts it too (Position.save runs the bay check).
            slot.container_visit = container_visit
            slot.save()

    def test_choose_slots_puts_45_foot_only_in_even_bays(self):
        visits = self.make_lengths('L5G1', ISOType.L45)
        forty_five = [cv for cv in visits if cv.container_number.iso_type.length == ISOType.L45]
        free = list(yard_planning.free_yard_slots(self.grid))
        self.assertTrue(any(slot.pile.bay % 2 == 1 for slot in free))

        chosen = yard_planning.choose_slots(free, len(forty_five), random.Random(2), containers=forty_five)

        self.assertEqual([slot.pile.bay % 2 for slot in chosen], [0] * len(forty_five))
        self.assertTrue(all(slot.pile.allows_45 for slot in chosen))

    def test_no_even_bay_free_refuses_45_foot(self):
        self.make_lengths('L5G1', ISOType.L45)
        hold_slots([slot for slot in all_block_slots() if slot.pile.bay % 2 == 0])
        with self.assertRaises(DischargePlanningError) as caught:
            plan_visit_discharge(self.visit)
        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.assertIn('fit the container sizes', caught.exception.planning_message)
        self.assertFalse(Order.objects.filter(order_type=Order.MOVE_IN).exists())


class FloatingGuardTests(TestCase):
    """BE review N-3 on #38: a lower tier that is missing, or empty without an
    operational position, does not hold up the tier above it; planning must
    not reserve that higher slot (a floating stack)."""

    containers = 1

    def setUp(self):
        reset_scenario('import_v1', containers=self.containers)
        drop_seeded_orders()
        self.visit = CarrierVisit.objects.get()
        # An odd (20') bay pile, since import_v1 discharges a 22G1.
        self.ground = next(slot for slot in ground_slots() if slot.pile.bay % 2 == 1)
        self.pile = self.ground.pile
        self.tier2 = Position.objects.get(pile=self.pile, tier=2)
        self.grid = self.pile.storage_grid
        # Everything else in the block is held, so only this pile is in play.
        all_block_slots().exclude(pk__in=[self.ground.pk, self.tier2.pk]).update(is_reserved=True)

    def detach_ground_operational_position(self):
        OperationalPosition.objects.filter(stack_position=self.ground).update(stack_position=None)

    def free(self):
        return list(yard_planning.free_yard_slots(self.grid))

    def test_empty_ground_without_operational_position_does_not_hold_up_tier_2(self):
        self.detach_ground_operational_position()
        free = self.free()
        self.assertEqual([slot.pk for slot in free], [self.tier2.pk])

        with self.assertRaises(yard_planning.SlotShortageError) as caught:
            yard_planning.choose_slots(free, 1, random.Random(0))
        self.assertEqual((caught.exception.needed, caught.exception.available), (1, 0))

    def test_missing_ground_position_does_not_hold_up_tier_2(self):
        Position.objects.filter(pk=self.ground.pk).delete()
        free = self.free()
        self.assertEqual([slot.pk for slot in free], [self.tier2.pk])

        with self.assertRaises(yard_planning.SlotShortageError):
            yard_planning.choose_slots(free, 1, random.Random(0))

    def test_plan_maps_the_floating_slot_to_no_free_slot_and_stays_arrived(self):
        self.detach_ground_operational_position()
        self.visit.status = CarrierVisit.IN_OPERATION

        with self.assertRaises(DischargePlanningError) as caught:
            self.visit.save()

        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.assertIn('reachable without a floating stack', caught.exception.planning_message)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertFalse(Order.objects.filter(order_type=Order.MOVE_IN).exists())

    def test_occupied_ground_without_operational_position_still_holds_up_tier_2(self):
        # Control: a lower tier that really holds a container supports the
        # tier above, whether or not it is plannable itself.
        self.detach_ground_operational_position()
        Position.objects.filter(pk=self.ground.pk).update(is_occupied=True)

        planned = plan_visit_discharge(self.visit)

        self.assertEqual(len(planned), 1)
        slot = Order.objects.get(pk=planned[0].order.pk).to_position.stack_position
        self.assertEqual(slot.pk, self.tier2.pk)


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
