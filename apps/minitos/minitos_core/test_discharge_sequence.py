"""BL-34a (D-079): discharge sequence by vessel slot.

Deck before hold; top tier first; within a tier the row nearest the quay
first, then the next row out, until the tier is cleared; then the tier below.
Planning order and order priority both follow it, so the release step (and
OES dispatch, which sorts by priority) do too.
"""

from django.test import SimpleTestCase, TestCase

from minitos_core.models import (
    CarrierVisit,
    ContainerUnit,
    ContainerVisit,
    OperationalPosition,
    Order,
)
from minitos_core.scenarios import reset_scenario, run_step
from minitos_core.scenarios.base import container_number
from minitos_core.scenarios.import_v1 import OWNER_CODE
from minitos_core.services.discharge_planning import (
    SIDE_PORT,
    SIDE_STARBOARD,
    discharge_container_visits,
    is_deck_tier,
    plan_visit_discharge,
    row_distance_from_quay,
    vessel_slot_discharge_key,
)


def slot(bay, row, tier):
    stow = f'{bay:03d}{row:02d}{tier:02d}'
    return OperationalPosition(logical_address={'bay': bay, 'row': row, 'tier': tier, 'stow': stow})


class RowDistanceFromQuayTests(SimpleTestCase):
    def test_port_side_alongside_outermost_port_row_is_nearest(self):
        rows = [1, 2, 3, 4, 0, 6, 5]
        self.assertEqual(sorted(rows, key=row_distance_from_quay), [6, 4, 2, 0, 1, 3, 5])
        self.assertEqual(
            sorted(rows, key=lambda row: row_distance_from_quay(row, SIDE_PORT)), [6, 4, 2, 0, 1, 3, 5],
        )

    def test_starboard_side_alongside_is_the_mirror(self):
        rows = [1, 2, 3, 4, 0, 6, 5]
        self.assertEqual(
            sorted(rows, key=lambda row: row_distance_from_quay(row, SIDE_STARBOARD)), [5, 3, 1, 0, 2, 4, 6],
        )

    def test_deck_and_hold_tiers(self):
        self.assertTrue(is_deck_tier(82))
        self.assertTrue(is_deck_tier(72))
        self.assertFalse(is_deck_tier(2))
        self.assertFalse(is_deck_tier(18))


class VesselSlotDischargeKeyTests(SimpleTestCase):
    def test_deck_before_hold_top_tier_first_nearest_row_first(self):
        slots = [slot(33, 1, 4), slot(33, 2, 82), slot(33, 1, 84), slot(33, 4, 84), slot(33, 2, 6), slot(33, 6, 82)]
        ordered = [slot.logical_address['stow'] for slot in sorted(slots, key=vessel_slot_discharge_key)]
        self.assertEqual(ordered, ['0330484', '0330184', '0330682', '0330282', '0330206', '0330104'])

    def test_stow_string_alone_is_enough(self):
        bare = OperationalPosition(logical_address={'stow': '0330486'})
        self.assertEqual(vessel_slot_discharge_key(bare), vessel_slot_discharge_key(slot(33, 4, 86)))

    def test_placeholder_slot_sorts_after_every_real_slot(self):
        placeholder = OperationalPosition(logical_address={'stow': None})
        self.assertGreater(vessel_slot_discharge_key(placeholder), vessel_slot_discharge_key(slot(1, 1, 2)))
        self.assertGreater(vessel_slot_discharge_key(None), vessel_slot_discharge_key(slot(1, 1, 2)))


class DischargeSequenceTestBase(TestCase):
    containers = 3

    def setUp(self):
        reset_scenario('import_v1', containers=self.containers)
        self.visit = CarrierVisit.objects.get()

    def vessel_slot(self, container_visit):
        return OperationalPosition.objects.get(
            position_kind=OperationalPosition.VESSEL_SLOT,
            logical_address__container_visit_id=container_visit.pk,
        )

    def stow_of(self, order):
        return order.from_position.logical_address['stow']

    def planned_orders(self):
        return list(Order.objects.filter(
            container_number__ib_carrier=self.visit, order_type=Order.MOVE_IN,
        ).select_related('from_position').order_by('priority', 'pk'))


class ThreeBoxDischargeTests(DischargeSequenceTestBase):
    """The seeded three boxes sit in one stack (bay 033, row 06, tiers 82, 84,
    86): the top box goes first, the bottom box last."""

    def test_plan_and_priority_go_top_tier_first(self):
        self.visit.status = CarrierVisit.IN_OPERATION
        self.visit.save()

        orders = self.planned_orders()
        self.assertEqual([self.stow_of(order) for order in orders], ['0330686', '0330684', '0330682'])
        priorities = [order.priority for order in orders]
        self.assertEqual(len(set(priorities)), 3)
        # Planning order (pk) matches the priority order.
        self.assertEqual([order.pk for order in orders], sorted(order.pk for order in orders))

    def test_plan_result_is_in_discharge_sequence(self):
        planned = plan_visit_discharge(self.visit)
        self.assertEqual(
            [item.order.from_position.logical_address['tier'] for item in planned], [86, 84, 82],
        )

    def test_release_step_releases_the_top_box_first(self):
        run_step('import_v1', 'visit-in-operation')
        released = [run_step('import_v1', 'release-discharge') for _ in range(3)]
        self.assertEqual([self.stow_of(order) for order in released], ['0330686', '0330684', '0330682'])


class MultiRowDischargeTests(DischargeSequenceTestBase):
    """Nine boxes over several rows, two deck tiers and two hold tiers, stowed
    in a shuffled container-visit (pk) order."""

    containers = 1
    # (row, tier) per extra container visit, in creation (pk) order.
    STOWAGE = ((1, 4), (2, 84), (0, 82), (5, 6), (4, 84), (3, 82), (2, 4), (1, 84), (6, 82))
    EXPECTED = (
        '0330484', '0330284', '0330184',  # deck, top tier: port-most row first
        '0330682', '0330082', '0330382',  # deck, tier below
        '0330506',                        # hold, top hold tier
        '0330204', '0330104',             # hold, tier below
    )

    def setUp(self):
        super().setUp()
        seeded = ContainerVisit.objects.get(ib_carrier=self.visit)
        template = self.vessel_slot(seeded)
        rows_tiers = list(self.STOWAGE)
        # Reuse the seeded box for the first stowage entry.
        self._restow(template, *rows_tiers.pop(0))
        for index, (row, tier) in enumerate(rows_tiers, start=1):
            unit = ContainerUnit.objects.create(
                container_number=container_number(OWNER_CODE, 700000 + index),
                iso_type=seeded.container_number.iso_type, owner=seeded.container_number.owner,
                is_in_terminal=False,
            )
            container_visit = ContainerVisit.objects.create(
                category=ContainerVisit.IMPORT, container_number=unit, ib_carrier=self.visit,
                location=seeded.location, eta=seeded.eta, weight=seeded.weight,
                status=ContainerVisit.PREADVISED, bill_of_lading=f'TVLBL34A{index:03d}',
            )
            position = OperationalPosition.objects.get(pk=template.pk)
            position.pk = None
            position.code = f'{self.visit.name}.BL34A.{index}'
            position.logical_address = dict(template.logical_address, container_visit_id=container_visit.pk)
            position.save()
            self._restow(position, row, tier)

    def _restow(self, position, row, tier):
        stow = f'033{row:02d}{tier:02d}'
        position.logical_address = dict(position.logical_address, row=row, tier=tier, stow=stow)
        position.save(update_fields=['logical_address'])

    def test_sequence_before_planning(self):
        sequence = discharge_container_visits(self.visit)
        stows = [self.vessel_slot(cv).logical_address['stow'] for cv in sequence]
        self.assertEqual(stows, list(self.EXPECTED))

    def test_plan_and_priority_follow_deck_tier_row_order(self):
        self.visit.status = CarrierVisit.IN_OPERATION
        self.visit.save()

        orders = self.planned_orders()
        self.assertEqual([self.stow_of(order) for order in orders], list(self.EXPECTED))
        self.assertEqual(len({order.priority for order in orders}), len(self.EXPECTED))
        self.assertEqual([order.pk for order in orders], sorted(order.pk for order in orders))

    def test_release_order_follows_the_sequence(self):
        run_step('import_v1', 'visit-in-operation')
        released = [run_step('import_v1', 'release-discharge') for _ in self.EXPECTED]
        self.assertEqual([self.stow_of(order) for order in released], list(self.EXPECTED))
