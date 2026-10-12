from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from minitos_core.models import Che, EquipmentType, Location


class GodotCheIntegrationTests(APITestCase):
    def setUp(self):
        user = User.objects.create_user(username="godot-test", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.location = Location.objects.create(
            name="Godot Integration Terminal",
            location_type="TL",
            latitude=41.35,
            longitude=2.17,
            map_origin_latitude=41.35,
            map_origin_longitude=2.17,
        )
        equipment_type = EquipmentType.objects.create(
            name="Integration Straddle Carrier",
            equipment_type=EquipmentType.SC,
            energy_source=EquipmentType.Diesel,
            fuel_tank_capacity=500.0,
        )
        self.che = Che.objects.create(
            name="SC-INTEGRATION-01",
            equipment_type=equipment_type,
            location=self.location,
        )
        self.url = reverse("minitos_core:che-detail", args=[self.location.id, self.che.id])

    def test_godot_snapshot_lifecycle_round_trips_through_tos(self):
        idle = self._patch_snapshot(True, False, True, False, False, 41.351, 2.172, 71.4, 98.0)
        self.assertTrue(idle["powered_on"])
        self.assertFalse(idle["working"])
        self.assertTrue(idle["is_available"])
        self.assertEqual(idle["tic40_activity_state"], "IDLE")

        working = self._patch_snapshot(True, True, False, True, False, 41.352, 2.173, 73.0, 97.5)
        self.assertTrue(working["working"])
        self.assertTrue(working["is_dispatched"])
        self.assertEqual(working["latitude"], 41.352)
        self.assertEqual(working["longitude"], 2.173)
        self.assertEqual(working["rotation"], 73.0)
        self.assertEqual(working["fuel_level"], 97.5)

        offline = self._patch_snapshot(False, False, False, False, False, 41.352, 2.173, 73.0, 97.5)
        self.assertFalse(offline["powered_on"])
        self.assertFalse(offline["is_available"])

        self.che.refresh_from_db()
        self.assertFalse(self.che.powered_on)
        self.assertFalse(self.che.is_available)

    def _patch_snapshot(self, powered_on, working, is_available, is_dispatched, is_in_maintenance, latitude, longitude, rotation, fuel_level):
        response = self.client.patch(
            self.url,
            {
                "powered_on": powered_on,
                "working": working,
                "is_available": is_available,
                "is_dispatched": is_dispatched,
                "is_in_maintenance": is_in_maintenance,
                "latitude": latitude,
                "longitude": longitude,
                "rotation": rotation,
                "fuel_level": fuel_level,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data
