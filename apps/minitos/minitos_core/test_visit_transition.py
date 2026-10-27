"""TCL-032: the visit / container-visit transition-status endpoints.

POST /minitos_core/api/visits/{id}/transition-status/ (import_v1 demo step 2)
and POST /minitos_core/api/container-visits/{id}/transition-status/.

On main both views referenced the undefined ``VisitStateManager`` (and the
visit view ``VisitSerializer``) and raised NameError (HTTP 500) for every
authenticated request that got past the input checks.
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from minitos_core.models import CarrierVisit, ContainerVisit, Location


class VisitTransitionStatusTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tcl032", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        location = Location.objects.create(
            name="TCL-032 Terminal", location_type="TL", latitude=41.35, longitude=2.17,
            map_origin_latitude=41.35, map_origin_longitude=2.17,
        )
        self.visit = CarrierVisit.objects.create(
            name="VISIT-032", location=location, status=CarrierVisit.ANNOUNCED,
        )

    def post(self, body, visit_id=None):
        return self.client.post(
            reverse("minitos_core:transition_visit_status", args=[visit_id or self.visit.pk]),
            body, format="json",
        )

    def test_demo_step_2_arrived_returns_200_with_the_updated_visit(self):
        response = self.post({"status": "Arrived"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(response.data["success"])
        self.assertTrue(response.data["message"])
        self.assertEqual(response.data["visit"]["id"], self.visit.pk)
        self.assertEqual(response.data["visit"]["status"], CarrierVisit.ARRIVED)
        self.assertEqual(response.data["visit"]["status_display"], "Arrived")
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)

    def test_same_status_is_a_200_no_op_that_does_not_save(self):
        # CarrierVisit.save() recomputes etd from eta + calculated duration and
        # bumps last_updated (auto_now); a same-status request must do neither.
        CarrierVisit.objects.filter(pk=self.visit.pk).update(
            eta=datetime(2026, 10, 27, 8, 0, tzinfo=dt_timezone.utc),
            moves_in=100, moves_out=100, crane_split=2,
            expected_productivity_per_crane=Decimal("25.00"),
        )
        self.visit.refresh_from_db()
        self.assertIsNotNone(self.visit.calculated_duration_minutes)
        sentinel_etd = datetime(2030, 1, 1, 0, 0, tzinfo=dt_timezone.utc)
        sentinel_updated = datetime(2020, 1, 1, 0, 0, tzinfo=dt_timezone.utc)
        CarrierVisit.objects.filter(pk=self.visit.pk).update(
            etd=sentinel_etd, last_updated=sentinel_updated,
        )

        response = self.post({"status": CarrierVisit.ANNOUNCED})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["visit"]["status"], CarrierVisit.ANNOUNCED)
        after = CarrierVisit.objects.get(pk=self.visit.pk)
        self.assertEqual(after.status, CarrierVisit.ANNOUNCED)
        self.assertEqual(after.etd, sentinel_etd)
        self.assertEqual(after.last_updated, sentinel_updated)

    def test_unknown_status_is_400_and_leaves_the_visit_unchanged(self):
        response = self.post({"status": "Docked"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertFalse(response.data["success"])
        self.assertIn("status", response.data["errors"])
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ANNOUNCED)

    def test_only_the_status_is_changed(self):
        response = self.post({"status": "Arrived", "name": "RENAMED", "vessel_name": "X"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.name, "VISIT-032")
        self.assertIsNone(self.visit.vessel_name)

    def test_missing_status_unknown_visit_and_anonymous_keep_their_codes(self):
        self.assertEqual(self.post({}).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.post({"status": "Arrived"}, visit_id=999_999).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        anonymous = APIClient().post(
            reverse("minitos_core:transition_visit_status", args=[self.visit.pk]),
            {"status": "Arrived"}, format="json",
        )
        self.assertIn(anonymous.status_code, {401, 403})


class ContainerVisitTransitionStatusTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tcl032-cv", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        location = Location.objects.create(
            name="TCL-032 CV Terminal", location_type="TL", latitude=41.35, longitude=2.17,
            map_origin_latitude=41.35, map_origin_longitude=2.17,
        )
        self.container_visit = ContainerVisit.objects.create(location=location, weight=1000)

    def post(self, body, container_id=None):
        return self.client.post(
            reverse(
                "minitos_core:transition_container_status",
                args=[container_id or self.container_visit.pk],
            ),
            body, format="json",
        )

    def test_valid_status_returns_200_with_the_updated_container_visit(self):
        response = self.post({"status": "Arrived"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(response.data["success"])
        self.assertTrue(response.data["message"])
        self.assertEqual(response.data["container_visit"]["pk"], self.container_visit.pk)
        self.assertEqual(response.data["container_visit"]["status"], ContainerVisit.ARRIVED)
        self.container_visit.refresh_from_db()
        self.assertEqual(self.container_visit.status, ContainerVisit.ARRIVED)

    def test_same_status_is_a_200_no_op(self):
        before = ContainerVisit.objects.get(pk=self.container_visit.pk)
        response = self.post({"status": ContainerVisit.PREADVISED})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["container_visit"]["status"], ContainerVisit.PREADVISED)
        after = ContainerVisit.objects.get(pk=self.container_visit.pk)
        self.assertEqual(after.status, ContainerVisit.PREADVISED)
        self.assertEqual(after.last_updated, before.last_updated)

    def test_unknown_status_is_400_and_leaves_the_container_visit_unchanged(self):
        response = self.post({"status": "Teleported"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertFalse(response.data["success"])
        self.assertIn("status", response.data["errors"])
        self.container_visit.refresh_from_db()
        self.assertEqual(self.container_visit.status, ContainerVisit.PREADVISED)

    def test_existing_on_equipment_rule_is_enforced_as_400(self):
        # ContainerVisitSerializer.validate: OnEquipment only via the execution pickup service.
        response = self.post({"status": ContainerVisit.ON_EQUIPMENT})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.container_visit.refresh_from_db()
        self.assertEqual(self.container_visit.status, ContainerVisit.PREADVISED)

    def test_only_the_status_is_changed(self):
        response = self.post({"status": "AtYard", "weight": 1, "booking": "X"})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.container_visit.refresh_from_db()
        self.assertEqual(self.container_visit.weight, 1000)
        self.assertIsNone(self.container_visit.booking)

    def test_missing_status_unknown_container_visit_and_anonymous_keep_their_codes(self):
        self.assertEqual(self.post({}).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.post({"status": "Arrived"}, container_id=999_999).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        anonymous = APIClient().post(
            reverse("minitos_core:transition_container_status", args=[self.container_visit.pk]),
            {"status": "Arrived"}, format="json",
        )
        self.assertIn(anonymous.status_code, {401, 403})
