from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from minitos_core.models import (
    BerthSegment, CarrierService, CarrierVisit, Che, EquipmentType, LayoutAsset,
    LayoutVersion, LinearAxis, LinearMarker, Location,
)
from minitos_core.services import offset_geographic, sync_berth_bollards


class BerthPlanningTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='berth-planner', password='test-password')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.location = Location.objects.create(
            name='Berth Test Terminal', location_type=Location.TL,
            latitude=41.35, longitude=2.17,
            map_origin_latitude=41.35, map_origin_longitude=2.17,
            timezone='Europe/Madrid',
        )
        self.layout = LayoutVersion.objects.create(
            location=self.location, version=1, name='Working layout', created_by=self.user,
        )

    def test_berth_segment_api_creates_ordered_bollard_markers(self):
        quay, axis = self._quay_axis(length_m=100)
        response = self.client.post(
            reverse('minitos_core:berth-segment-list', args=[self.location.id]),
            {
                'quay_asset': quay.id, 'quay_face_axis': axis.id,
                'code': 'NORTH', 'name': 'North Berth',
                'start_chainage_m': 0, 'end_chainage_m': 100,
                'water_side': BerthSegment.WATER_LEFT,
                'bollard_direction': BerthSegment.BOLLARDS_FORWARD,
                'bollard_count': 5, 'default_bollard_spacing_m': 20,
                'start_margin_m': 5, 'end_margin_m': 5,
                'bollard_spacings': [18, 20, 22, 20],
            }, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        berth = BerthSegment.objects.get(pk=response.data['id'])
        self.assertEqual(berth.bollards.count(), 5)
        self.assertEqual(
            list(berth.bollards.values_list('chainage_m', flat=True)),
            [5.0, 23.0, 43.0, 65.0, 85.0],
        )
        self.assertEqual(response.data['bollards'][1]['spacing_from_previous_m'], 18.0)
        self.assertAlmostEqual(berth.bollards.first().longitude, 2.17)
        self.assertGreater(berth.bollards.last().latitude, berth.bollards.first().latitude)

    def test_berth_update_reverses_numbering_and_repositions_markers(self):
        berth = self._berth(length_m=180, count=4)
        response = self.client.patch(
            reverse('minitos_core:berth-segment-detail', args=[self.location.id, berth.id]),
            {
                'water_side': BerthSegment.WATER_RIGHT,
                'bollard_direction': BerthSegment.BOLLARDS_REVERSE,
                'bollard_count': 4, 'default_bollard_spacing_m': 50,
                'start_margin_m': 10, 'end_margin_m': 10,
                'bollard_spacings': [40, 50, 60],
            }, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        berth.refresh_from_db()
        for actual, expected in zip(
            berth.bollards.values_list('chainage_m', flat=True),
            [170.0, 130.0, 80.0, 20.0],
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(berth.water_side, BerthSegment.WATER_RIGHT)
        self.assertEqual(berth.bollard_direction, BerthSegment.BOLLARDS_REVERSE)

    def test_service_assignment_and_weekly_visit_generation_are_idempotent(self):
        berth = self._berth(length_m=300, count=16)
        bollards = list(berth.bollards)
        sts = EquipmentType.objects.create(
            name='Test STS', equipment_type=EquipmentType.STS, is_quay_equipment=True,
        )
        rtg = EquipmentType.objects.create(
            name='Test RTG', equipment_type=EquipmentType.RTG, is_yard_equipment=True,
        )
        Che.objects.create(name='STS-TEST-01', equipment_type=sts, location=self.location)
        Che.objects.create(name='RTG-TEST-01', equipment_type=rtg, location=self.location)
        service_response = self.client.post(
            reverse('minitos_core:carrier-service-list'),
            {
                'name': 'VS-1', 'location': self.location.id,
                'carrier_service_type': CarrierService.VESSEL,
                'start_date': '2026-06-01', 'repeat': CarrierService.REPEAT_WEEKLY,
                'repeat_weekly': 1, 'on_weekdays': 'Monday',
                'window_start_weekday': 'Monday', 'window_start_time': '08:00:00',
                'window_end_weekday': 'Tuesday', 'window_end_time': '21:00:00',
                'moves_in': 400, 'moves_out': 350, 'vessel_length_m': 280,
                'default_berth': berth.id, 'default_start_bollard': bollards[0].id,
                'primary_equipment_type': sts.id, 'crane_split': '3.20',
                'expected_productivity_per_crane': '25.00',
                'horizontal_transport_per_crane': '3.00',
                'equipment_requirements': [{
                    'equipment_type': rtg.id, 'equipment_split': '4.50',
                    'expected_productivity_per_hour': '18.00',
                    'horizontal_transport_per_unit': '0.00',
                }],
            }, format='json',
        )
        self.assertEqual(service_response.status_code, status.HTTP_201_CREATED, service_response.data)
        service_id = service_response.data['id']
        generate_url = reverse('minitos_core:carrier-service-generate-visits', args=[service_id])
        first = self.client.post(generate_url, {'start_date': '2026-06-01', 'end_date': '2026-06-15'}, format='json')
        second = self.client.post(generate_url, {'start_date': '2026-06-01', 'end_date': '2026-06-15'}, format='json')

        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(first.data['created_count'], 3)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertEqual(second.data['created_count'], 0)
        visits = CarrierVisit.objects.filter(carrier_service_id=service_id).order_by('eta')
        self.assertEqual(visits.count(), 3)
        self.assertEqual(visits.first().planned_berth, berth)
        self.assertEqual(visits.first().berth_start_bollard, bollards[0])
        self.assertEqual(visits.first().berth_end_bollard, bollards[14])
        self.assertEqual(visits.first().total_moves, 750)
        self.assertEqual(visits.first().calculated_duration_minutes, 563)
        self.assertEqual(visits.first().calculated_duration_display, '9 hours and 23 minutes')
        self.assertEqual(visits.first().etd.astimezone(ZoneInfo('Europe/Madrid')).hour, 17)
        self.assertEqual(visits.first().equipment_requirements.get().equipment_type, rtg)

    def test_berth_plan_and_editor_render_new_layout_controls(self):
        berth = self._berth()
        CarrierService.objects.create(
            name='Rendered Service', location=self.location,
            carrier_service_type=CarrierService.VESSEL,
            start_date=date(2026, 6, 1), repeat=CarrierService.REPEAT_WEEKLY,
            repeat_weekly=1, on_weekdays='Monday', moves_in=100, moves_out=80,
            vessel_length_m=100, default_berth=berth,
            default_start_bollard=berth.bollards.first(),
            default_end_bollard=berth.bollards.last(),
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('berth_plan', args=[self.location.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, 'Rendered Service')
        self.assertContains(response, 'Europe/Madrid')
        self.assertContains(response, 'Operational Productivity')

        editor = self.client.get(reverse('terminal_editor', args=[self.location.id]))
        self.assertEqual(editor.status_code, status.HTTP_200_OK)
        self.assertContains(editor, 'waterSide')
        self.assertContains(editor, 'bollardDirection')
        self.assertContains(editor, 'Movement axes')
        self.assertContains(editor, 'Transfer interfaces')
        self.assertContains(editor, 'interfaceRoadAxis')
        self.assertContains(editor, 'roadDirection')
        self.assertContains(editor, 'roadLanes')
        self.assertContains(editor, 'terminal_editor.js')

    def test_vessel_service_rejects_length_that_does_not_fit(self):
        berth = self._berth()
        response = self.client.post(
            reverse('minitos_core:carrier-service-list'),
            {
                'name': 'TOO-LONG', 'location': self.location.id,
                'carrier_service_type': CarrierService.VESSEL,
                'start_date': '2026-06-01', 'default_berth': berth.id,
                'default_start_bollard': berth.bollards.first().id,
                'vessel_length_m': 500,
            }, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('vessel_length_m', response.data)

    def test_visit_productivity_override_recalculates_etd(self):
        eta = datetime(2026, 6, 1, 8, 0, tzinfo=ZoneInfo('Europe/Madrid'))
        create = self.client.post(
            reverse('minitos_core:carrier-visit-list', args=[self.location.id]),
            {
                'name': 'PRODUCTIVITY-TEST', 'visit_type': CarrierVisit.VESSEL,
                'location': self.location.id, 'eta': eta.isoformat(),
                'total_moves': 320, 'crane_split': '2.00',
                'expected_productivity_per_crane': '20.00',
            }, format='json',
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.data)
        self.assertEqual(create.data['calculated_duration_minutes'], 480)
        update = self.client.patch(
            reverse('minitos_core:carrier-visit-detail', args=[self.location.id, create.data['id']]),
            {'crane_split': '4.00'}, format='json',
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.data)
        self.assertEqual(update.data['calculated_duration_minutes'], 240)

    def test_location_api_exposes_and_validates_terminal_timezone(self):
        response = self.client.get(reverse('minitos_core:location-detail', args=[self.location.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['timezone'], 'Europe/Madrid')
        self.assertIn('local_datetime', response.data)
        self.client.force_login(self.user)
        invalid = self.client.post(
            reverse('minitos_core:location_api_save', args=[self.location.id]),
            {'name': self.location.name, 'timezone': 'Mars/Olympus'}, format='json',
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

    def _quay_axis(self, length_m=300):
        end_latitude, end_longitude = offset_geographic(41.35, 2.17, 0, length_m)
        points = [[2.17, 41.35], [end_longitude, end_latitude]]
        quay = LayoutAsset.objects.create(
            layout_version=self.layout, code=f'Q-{length_m}', name='Quay A',
            asset_kind=LayoutAsset.QUAY,
            geometry={'type': 'LineString', 'coordinates': points},
            metadata={'width_m': 20},
        )
        axis = LinearAxis.objects.create(
            asset=quay, code=f'Q-{length_m}.FACE', name='Quay face',
            axis_kind=LinearAxis.QUAY_FACE, points=points,
        )
        return quay, axis

    def _berth(self, length_m=300, count=16):
        quay, axis = self._quay_axis(length_m)
        berth = BerthSegment.objects.create(
            quay_asset=quay, quay_face_axis=axis, code='BERTH-A', name='Berth A',
            start_chainage_m=0, end_chainage_m=length_m,
        )
        return sync_berth_bollards(
            berth, bollard_count=count,
            default_bollard_spacing_m=length_m / (count - 1),
        )
