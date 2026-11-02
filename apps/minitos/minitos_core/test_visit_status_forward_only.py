"""D-025: vessel-visit status only moves forward, one step at a time.

Order: Created -> Announced -> Arrived -> InOperation -> Complete -> Departed.
A backward jump is refused with ``visit_status_backward``, a skipped one with
``visit_status_skipped``; setting the current status again is a no-op. The rule
lives on ``CarrierVisit`` (``clean()`` and ``save()``), so it holds for the
transition-status API, the carrier-visit REST API and the Django admin form.
"""

from itertools import pairwise

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from minitos_core.models import CarrierVisit, Location, VisitStatusTransitionError

ORDER = list(CarrierVisit.STATUS_ORDER)
FORWARD_STEPS = list(pairwise(ORDER))
BACKWARD = [(ORDER[i], ORDER[j]) for i in range(len(ORDER)) for j in range(i)]
SKIPS = [(ORDER[i], ORDER[j]) for i in range(len(ORDER)) for j in range(i + 2, len(ORDER))]


def make_location(name):
    return Location.objects.create(
        name=name, location_type="TL", latitude=41.35, longitude=2.17,
        map_origin_latitude=41.35, map_origin_longitude=2.17,
    )


class StatusOrderTests(TestCase):
    def test_order_matches_d025(self):
        self.assertEqual(
            ORDER, ["Created", "Announced", "Arrived", "InOperation", "Complete", "Departed"],
        )
        self.assertEqual(ORDER, [value for value, _ in CarrierVisit.STATUS_CHOICES])

    def test_allowed_next_statuses(self):
        for current, nxt in FORWARD_STEPS:
            self.assertEqual(CarrierVisit.allowed_next_statuses(current), [nxt])
        self.assertEqual(CarrierVisit.allowed_next_statuses(CarrierVisit.DEPARTED), [])

    def test_case_lists_are_complete(self):
        # 5 forward steps, 15 backward pairs, 10 skips, 6 no-ops = 36 = 6 * 6.
        self.assertEqual(len(FORWARD_STEPS) + len(BACKWARD) + len(SKIPS) + len(ORDER), 36)


class ModelSaveTests(TestCase):
    """The model refuses on save(), whatever the caller (services, shell, API, admin)."""

    def setUp(self):
        self.location = make_location("D-025 Model Terminal")

    def visit_at(self, status_value, name="V-D025"):
        CarrierVisit.objects.filter(name=name).delete()
        return CarrierVisit.objects.create(name=name, location=self.location, status=status_value)

    def test_a_new_visit_may_start_at_any_status(self):
        # Creation is not a transition (the import_v1 seed creates Arrived, D-018).
        for i, value in enumerate(ORDER):
            visit = CarrierVisit.objects.create(name=f"NEW-{i}", location=self.location, status=value)
            self.assertEqual(visit.status, value)

    def test_each_forward_step_saves(self):
        for old, new in FORWARD_STEPS:
            with self.subTest(step=f"{old}->{new}"):
                visit = self.visit_at(old)
                visit.status = new
                visit.full_clean()
                visit.save()
                visit.refresh_from_db()
                self.assertEqual(visit.status, new)

    def test_walks_the_whole_lifecycle_on_one_visit(self):
        visit = self.visit_at(CarrierVisit.PLANNED)
        for _, new in FORWARD_STEPS:
            visit.status = new
            visit.save()
        visit.refresh_from_db()
        self.assertEqual(visit.status, CarrierVisit.DEPARTED)

    def assert_refused(self, old, new, code):
        visit = self.visit_at(old)
        visit.status = new
        with self.assertRaises(VisitStatusTransitionError) as ctx:
            visit.save()
        error = ctx.exception
        self.assertEqual(error.transition_code, code)
        self.assertEqual((error.from_status, error.to_status), (old, new))
        self.assertEqual(error.allowed_next, CarrierVisit.allowed_next_statuses(old))
        self.assertEqual(error.message_dict["status"], [error.transition_message])
        self.assertIn(old, error.transition_message)
        self.assertIn(new, error.transition_message)
        self.assertIn("D-025", error.transition_message)
        self.assertEqual(CarrierVisit.objects.get(pk=visit.pk).status, old)
        # clean() (used by ModelForm / the admin) refuses the same way.
        with self.assertRaises(ValidationError) as clean_ctx:
            visit.full_clean()
        self.assertEqual(clean_ctx.exception.error_dict["status"][0].code, code)

    def test_backward_is_refused(self):
        for old, new in BACKWARD:
            with self.subTest(step=f"{old}->{new}"):
                self.assert_refused(old, new, CarrierVisit.STATUS_BACKWARD)

    def test_skip_is_refused(self):
        for old, new in SKIPS:
            with self.subTest(step=f"{old}->{new}"):
                self.assert_refused(old, new, CarrierVisit.STATUS_SKIPPED)

    def test_messages_name_the_allowed_next_status(self):
        visit = self.visit_at(CarrierVisit.ARRIVED)
        visit.status = CarrierVisit.DEPARTED
        with self.assertRaises(VisitStatusTransitionError) as ctx:
            visit.save()
        self.assertEqual(
            ctx.exception.transition_message,
            "Visit V-D025 cannot skip from Arrived to Departed: vessel-visit status moves one "
            "step at a time (Created, Announced, Arrived, InOperation, Complete, Departed; "
            "D-025). The only allowed next status is InOperation.",
        )
        visit = self.visit_at(CarrierVisit.DEPARTED)
        visit.status = CarrierVisit.COMPLETE
        with self.assertRaises(VisitStatusTransitionError) as ctx:
            visit.save()
        self.assertEqual(
            ctx.exception.transition_message,
            "Visit V-D025 cannot go back from Departed to Complete: vessel-visit status only "
            "moves forward (D-025). Departed is the final status.",
        )

    def test_same_status_save_is_allowed(self):
        for value in ORDER:
            with self.subTest(status=value):
                visit = self.visit_at(value)
                visit.vessel_name = "SAME"
                visit.full_clean()
                visit.save()
                self.assertEqual(CarrierVisit.objects.get(pk=visit.pk).status, value)

    def test_save_with_update_fields_without_status_skips_the_check(self):
        visit = self.visit_at(CarrierVisit.IN_OPERATION)
        visit.status = CarrierVisit.PLANNED  # stale in memory, not written
        visit.vessel_name = "ONLY NAME"
        visit.save(update_fields=["vessel_name"])
        stored = CarrierVisit.objects.get(pk=visit.pk)
        self.assertEqual((stored.status, stored.vessel_name), (CarrierVisit.IN_OPERATION, "ONLY NAME"))

    def test_stale_instance_cannot_write_an_older_status(self):
        visit = self.visit_at(CarrierVisit.ARRIVED)
        stale = CarrierVisit.objects.get(pk=visit.pk)
        visit.status = CarrierVisit.IN_OPERATION
        visit.save()
        stale.vessel_name = "STALE"  # stale.status is still Arrived in memory
        with self.assertRaises(VisitStatusTransitionError) as ctx:
            stale.save()
        self.assertEqual(ctx.exception.transition_code, CarrierVisit.STATUS_BACKWARD)
        self.assertEqual(CarrierVisit.objects.get(pk=visit.pk).status, CarrierVisit.IN_OPERATION)


class TransitionStatusApiTests(APITestCase):
    """POST /minitos_core/api/visits/{id}/transition-status/ (TCL-032 endpoint)."""

    def setUp(self):
        self.user = User.objects.create_user(username="d025-api", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.location = make_location("D-025 API Terminal")

    def visit_at(self, status_value):
        CarrierVisit.objects.filter(name="V-API").delete()
        return CarrierVisit.objects.create(name="V-API", location=self.location, status=status_value)

    def post(self, visit, new_status):
        return self.client.post(
            reverse("minitos_core:transition_visit_status", args=[visit.pk]),
            {"status": new_status}, format="json",
        )

    def test_each_forward_step_is_200(self):
        for old, new in FORWARD_STEPS:
            with self.subTest(step=f"{old}->{new}"):
                visit = self.visit_at(old)
                response = self.post(visit, new)
                self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
                self.assertTrue(response.data["success"])
                self.assertEqual(response.data["visit"]["status"], new)
                self.assertEqual(CarrierVisit.objects.get(pk=visit.pk).status, new)

    def test_demo_arrived_to_in_operation(self):
        visit = self.visit_at(CarrierVisit.ARRIVED)
        response = self.post(visit, "InOperation")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["message"], "Visit V-API status changed from Arrived to InOperation.")

    def assert_409(self, old, new, code):
        visit = self.visit_at(old)
        response = self.post(visit, new)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.content)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["code"], code)
        self.assertEqual(response.data["from_status"], old)
        self.assertEqual(response.data["to_status"], new)
        self.assertEqual(response.data["allowed_next"], CarrierVisit.allowed_next_statuses(old))
        self.assertIn("D-025", response.data["message"])
        self.assertEqual(CarrierVisit.objects.get(pk=visit.pk).status, old)

    def test_backward_is_409_visit_status_backward(self):
        for old, new in BACKWARD:
            with self.subTest(step=f"{old}->{new}"):
                self.assert_409(old, new, "visit_status_backward")

    def test_skip_is_409_visit_status_skipped(self):
        for old, new in SKIPS:
            with self.subTest(step=f"{old}->{new}"):
                self.assert_409(old, new, "visit_status_skipped")

    def test_demo_step_3_invalid_changes_from_arrived(self):
        # Demo script step 3: "e.g. to Departed, or back to Created".
        self.assert_409("Arrived", "Departed", "visit_status_skipped")
        self.assert_409("Arrived", "Created", "visit_status_backward")

    def test_same_status_is_still_a_200_no_op(self):
        for value in ORDER:
            with self.subTest(status=value):
                visit = self.visit_at(value)
                before = CarrierVisit.objects.get(pk=visit.pk).last_updated
                response = self.post(visit, value)
                self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
                self.assertTrue(response.data["success"])
                self.assertIn("no change", response.data["message"])
                self.assertEqual(CarrierVisit.objects.get(pk=visit.pk).last_updated, before)

    def test_unknown_status_stays_400(self):
        visit = self.visit_at(CarrierVisit.ARRIVED)
        response = self.post(visit, "Docked")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertEqual(response.data["code"], "visit_status_unknown")
        self.assertIn("status", response.data["errors"])


class CarrierVisitRestApiTests(APITestCase):
    """PATCH/PUT on /minitos_core/locations/{id}/carrier-visits/{id}/ also refuses (400)."""

    def setUp(self):
        self.user = User.objects.create_user(username="d025-rest", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.location = make_location("D-025 REST Terminal")
        self.visit = CarrierVisit.objects.create(
            name="V-REST", location=self.location, status=CarrierVisit.ARRIVED,
        )

    def url(self):
        return reverse(
            "minitos_core:carrier-visit-detail",
            kwargs={"location_pk": self.location.pk, "pk": self.visit.pk},
        )

    def test_patch_forward_step(self):
        response = self.client.patch(self.url(), {"status": "InOperation"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.IN_OPERATION)

    def test_patch_backward_and_skip_are_400_with_the_d025_message(self):
        for new, code in (("Announced", "visit_status_backward"), ("Complete", "visit_status_skipped")):
            with self.subTest(to=new):
                response = self.client.patch(self.url(), {"status": new}, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
                self.assertIn("D-025", str(response.data["status"][0]))
                self.assertEqual(response.data["status"][0].code, code)
                self.visit.refresh_from_db()
                self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)

    def test_patch_same_status_and_other_fields_is_200(self):
        response = self.client.patch(
            self.url(), {"status": "Arrived", "vessel_name": "SAME STATUS"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.visit.refresh_from_db()
        self.assertEqual((self.visit.status, self.visit.vessel_name), ("Arrived", "SAME STATUS"))


class AdminPanelTests(TestCase):
    """The Django admin change form (Yael's demo: Arrived -> InOperation in the admin)."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="d025-admin", email="admin@terminal-scms.invalid", password="test-password",
        )
        self.client.force_login(self.admin_user)
        self.location = make_location("D-025 Admin Terminal")
        self.visit = CarrierVisit.objects.create(
            name="V-ADMIN", location=self.location, status=CarrierVisit.ARRIVED,
        )
        self.model_admin = admin.site._registry[CarrierVisit]

    def change_url(self):
        return reverse("admin:minitos_core_carriervisit_change", args=[self.visit.pk])

    def form_data(self, **overrides):
        """The change form's current values, as the browser would post them."""
        request = RequestFactory().get(self.change_url())
        request.user = self.admin_user
        form = self.model_admin.get_form(request, self.visit)(instance=self.visit)
        data = {}
        for name, field in form.fields.items():
            value = form[name].value()
            if value is None:
                continue
            if hasattr(field, "fields"):  # split datetime widgets
                continue
            data[name] = value
        data.update(overrides)
        return data

    def admin_form(self, new_status):
        request = RequestFactory().post(self.change_url())
        request.user = self.admin_user
        form_class = self.model_admin.get_form(request, self.visit)
        return form_class(data=self.form_data(status=new_status), instance=CarrierVisit.objects.get(pk=self.visit.pk))

    def test_admin_form_each_forward_step_is_valid(self):
        for old, new in FORWARD_STEPS:
            with self.subTest(step=f"{old}->{new}"):
                CarrierVisit.objects.filter(pk=self.visit.pk).update(status=old)
                self.visit.refresh_from_db()
                form = self.admin_form(new)
                self.assertTrue(form.is_valid(), form.errors)
                form.save()
                self.assertEqual(CarrierVisit.objects.get(pk=self.visit.pk).status, new)

    def test_admin_form_backward_and_skip_are_invalid_on_the_status_field(self):
        for old, new in BACKWARD + SKIPS:
            with self.subTest(step=f"{old}->{new}"):
                CarrierVisit.objects.filter(pk=self.visit.pk).update(status=old)
                self.visit.refresh_from_db()
                form = self.admin_form(new)
                self.assertFalse(form.is_valid())
                self.assertIn("status", form.errors)
                code = "visit_status_backward" if ORDER.index(new) < ORDER.index(old) else "visit_status_skipped"
                self.assertEqual(form.errors.as_data()["status"][0].code, code)
                self.assertEqual(CarrierVisit.objects.get(pk=self.visit.pk).status, old)

    def test_admin_form_same_status_is_valid(self):
        form = self.admin_form(CarrierVisit.ARRIVED)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(CarrierVisit.objects.get(pk=self.visit.pk).status, CarrierVisit.ARRIVED)

    def test_admin_change_page_moves_arrived_to_in_operation(self):
        response = self.client.post(self.change_url(), self.form_data(status="InOperation"))
        self.assertEqual(response.status_code, 302, response.content[:2000])
        self.assertEqual(CarrierVisit.objects.get(pk=self.visit.pk).status, CarrierVisit.IN_OPERATION)

    def test_admin_change_page_refuses_backward_with_the_message(self):
        response = self.client.post(self.change_url(), self.form_data(status="Created"))
        self.assertEqual(response.status_code, 200)  # form re-rendered with errors
        self.assertContains(response, "cannot go back from Arrived to Created")
        self.assertContains(response, "D-025")
        self.assertEqual(CarrierVisit.objects.get(pk=self.visit.pk).status, CarrierVisit.ARRIVED)

    def test_admin_change_page_refuses_skip_with_the_message(self):
        response = self.client.post(self.change_url(), self.form_data(status="Departed"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot skip from Arrived to Departed")
        self.assertContains(response, "The only allowed next status is InOperation.")
        self.assertEqual(CarrierVisit.objects.get(pk=self.visit.pk).status, CarrierVisit.ARRIVED)
