"""TCL-032: POST /minitos_core/api/visits/{id}/transition-status/ (import_v1 demo step 2).

On main the view referenced the undefined ``VisitStateManager`` and
``VisitSerializer`` and raised NameError (HTTP 500) for every authenticated
request that got past the input checks.
"""

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from minitos_core.models import CarrierVisit, Location


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
