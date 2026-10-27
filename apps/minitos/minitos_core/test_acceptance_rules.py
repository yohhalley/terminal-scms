"""Acceptance rule D-015 (vault '03 Decisions', Yael, 2026-10-25).

D-015: while a container is on a CHE, its general position is "On Equipment" and
its precise position is the CHE id (QC01, QC02, TT01, RTG01...), for every CHE.
"""

from django.db import transaction
from django.urls import reverse
from rest_framework import status

from minitos_core import test_contract_execution_events as contract
from minitos_core.models import Che, ContainerVisit, EquipmentType, Position
from minitos_core.services import drop_container, pickup_container


class _Rollback(Exception):
    pass


class OnEquipmentLocationTests(contract.GoldenExecutionEventConsumerTests):
    # Reuse the fixture only; the contract tests run in their own module.
    test_golden_fixtures_cover_one_full_leg = None
    test_golden_envelopes_carry_terminal_id = None
    test_golden_leg_applies_through_real_consumer = None

    def visit_api(self):
        response = self.client.get(reverse(
            'minitos_core:container_visit-detail', args=[self.location.pk, self.visit.pk],
        ))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_d015_container_on_any_che_is_on_equipment_with_che_id(self):
        """Given a container in a yard slot and a CHE named QC01, TT01 or RTG01,
        When that CHE picks the container up,
        Then the general position is 'On Equipment', the precise position is the
        CHE id, and no yard slot still holds the container."""
        for code, kind in (
            ('QC01', EquipmentType.STS), ('TT01', EquipmentType.TT), ('RTG01', EquipmentType.RTG),
        ):
            with self.subTest(che=code):
                try:
                    with transaction.atomic():
                        equipment_type = EquipmentType.objects.create(
                            name=f'Type {code}', equipment_type=kind,
                        )
                        che = Che.objects.create(
                            name=code, equipment_type=equipment_type, location=self.location,
                            powered_on=True, is_available=True, latitude=40, longitude=-3,
                        )
                        pickup_container(self.visit.pk, che.pk)
                        self.visit.refresh_from_db()
                        self.assertEqual(self.visit.status, ContainerVisit.ON_EQUIPMENT)
                        self.assertEqual(self.visit.get_status_display(), 'On Equipment')
                        self.assertEqual(self.visit.current_che.name, code)
                        self.assertFalse(Position.objects.filter(container_visit=self.visit).exists())

                        data = self.visit_api()
                        self.assertEqual(data['status'], ContainerVisit.ON_EQUIPMENT)
                        self.assertEqual(data['current_che'], che.pk)
                        self.assertEqual(data['current_che_name'], code)
                        self.assertIsNone(data['position'])
                        raise _Rollback
                except _Rollback:
                    pass

    def test_d015_drop_clears_the_on_equipment_location(self):
        """Given a container on the fixture CHE, When it is dropped in a yard slot,
        Then it is no longer 'On Equipment' and has no CHE."""
        pickup_container(self.visit.pk, self.che.pk)
        drop_container(self.visit.pk, self.che.pk, self.target.pk)
        data = self.visit_api()
        self.assertEqual(data['status'], ContainerVisit.AT_YARD)
        self.assertIsNone(data['current_che'])
        self.assertIsNone(data['current_che_name'])
