"""TCL-009 (#4): vessel-visit admin panel.

The panel at ``/terminals/<id>/vessel-visits/`` lists a terminal's vessel
visits with status, eta/etd and ata/atd, and offers one POST button per status
that D-025 allows next (``CarrierVisit.allowed_next_statuses``). The POST
re-checks D-025 on the server and saves through ``CarrierVisit.save()``, so
save hooks on InOperation (TCL-010 discharge planning) run, and any
``ValidationError`` they raise rolls the change back and is shown.
"""

import re
from datetime import UTC, datetime
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from minitos_core.models import CarrierVisit, Location, Order
from minitos_core.scenarios import reset_scenario

ORDER = list(CarrierVisit.STATUS_ORDER)
FORM_RE = re.compile(
    r'<form method="post" action="(?P<action>[^"]+)">.*?name="status" value="(?P<status>[^"]+)"',
    re.DOTALL,
)


def make_location(name):
    return Location.objects.create(
        name=name, location_type="TL", latitude=41.35, longitude=2.17,
        map_origin_latitude=41.35, map_origin_longitude=2.17, timezone="UTC",
    )


def panel_url(location):
    return reverse("vessel_visit_panel", args=[location.pk])


def transition_url(location, visit):
    return reverse("vessel_visit_transition", args=[location.pk, visit.pk])


def row_html(html, visit_name):
    """The panel row for one visit (from its data-visit marker to the next row)."""
    start = html.index(f'data-visit="{visit_name}"')
    following = html.find('class="visit-row"', start)
    end = following if following != -1 else html.index("</section>", start)
    return html[start:end]


def offered_statuses(html, visit_name):
    return [match["status"] for match in FORM_RE.finditer(row_html(html, visit_name))]


class PanelTestCase(TestCase):
    def setUp(self):
        self.location = make_location("TCL-009 Terminal")
        self.user = User.objects.create_user(username="tcl009", password="test-password")
        self.client.force_login(self.user)

    def make_visit(self, name, status=CarrierVisit.PLANNED, **extra):
        return CarrierVisit.objects.create(
            name=name, status=status, location=extra.pop("location", self.location), **extra,
        )


class PanelListTests(PanelTestCase):
    def test_lists_vessel_visits_with_status_and_actual_times(self):
        self.make_visit(
            "TCL009-A", status=CarrierVisit.ARRIVED, vessel_name="MSC Demo",
            ata=datetime(2026, 11, 2, 8, 30, tzinfo=UTC),
        )
        self.make_visit(
            "TCL009-D", status=CarrierVisit.DEPARTED,
            ata=datetime(2026, 11, 1, 6, 0, tzinfo=UTC),
            atd=datetime(2026, 11, 1, 18, 45, tzinfo=UTC),
        )
        response = self.client.get(panel_url(self.location))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        arrived = row_html(html, "TCL009-A")
        self.assertIn('data-status="Arrived"', arrived)
        self.assertIn("MSC Demo", arrived)
        self.assertIn('<span data-field="ata">2026-11-02 08:30</span>', arrived)
        self.assertIn('<span data-field="atd">—</span>', arrived)
        departed = row_html(html, "TCL009-D")
        self.assertIn('data-status="Departed"', departed)
        self.assertIn('<span data-field="atd">2026-11-01 18:45</span>', departed)
        self.assertIn("Final status", departed)

    def test_times_are_shown_in_the_terminal_timezone(self):
        self.location.timezone = "Europe/Madrid"
        self.location.save()
        self.make_visit(
            "TCL009-TZ", status=CarrierVisit.ARRIVED,
            ata=datetime(2026, 11, 2, 8, 30, tzinfo=UTC),
        )
        html = self.client.get(panel_url(self.location)).content.decode()
        self.assertIn("times in Europe/Madrid", html)
        self.assertIn('<span data-field="ata">2026-11-02 09:30</span>', row_html(html, "TCL009-TZ"))

    def test_only_this_terminals_vessel_visits_are_listed(self):
        other = make_location("TCL-009 Other Terminal")
        self.make_visit("TCL009-MINE")
        self.make_visit("TCL009-TRUCK", visit_type=CarrierVisit.TRUCK)
        self.make_visit("TCL009-OTHER", location=other)
        html = self.client.get(panel_url(self.location)).content.decode()
        self.assertIn('data-visit="TCL009-MINE"', html)
        self.assertNotIn("TCL009-TRUCK", html)
        self.assertNotIn("TCL009-OTHER", html)

    def test_empty_terminal(self):
        html = self.client.get(panel_url(self.location)).content.decode()
        self.assertIn("No vessel visits at this terminal.", html)

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(panel_url(self.location))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response["Location"])

    def test_unknown_terminal_is_404(self):
        self.assertEqual(self.client.get(reverse("vessel_visit_panel", args=[999999])).status_code, 404)

    def test_terminal_browser_links_to_the_panel(self):
        html = self.client.get(reverse("terminal_browser")).content.decode()
        self.assertIn(f'href="{panel_url(self.location)}"', html)


class PanelButtonTests(PanelTestCase):
    def test_buttons_are_exactly_the_d025_allowed_next_statuses(self):
        for current in ORDER:
            self.make_visit(f"TCL009-{current}", status=current)
        html = self.client.get(panel_url(self.location)).content.decode()
        for current in ORDER:
            with self.subTest(current=current):
                self.assertEqual(
                    offered_statuses(html, f"TCL009-{current}"),
                    CarrierVisit.allowed_next_statuses(current),
                )

    def test_buttons_follow_the_skip_switch(self):
        # The panel reads D-025 from the model, so turning the skip rule off
        # (pending E4) offers every later status without a panel change.
        self.make_visit("TCL009-SW", status=CarrierVisit.ARRIVED)
        with mock.patch.object(CarrierVisit, "REFUSE_SKIPPED_STEPS", False):
            html = self.client.get(panel_url(self.location)).content.decode()
        self.assertEqual(offered_statuses(html, "TCL009-SW"), ["InOperation", "Complete", "Departed"])

    def test_buttons_post_to_the_visit_transition_url_with_a_csrf_token(self):
        visit = self.make_visit("TCL009-CSRF", status=CarrierVisit.ARRIVED)
        html = self.client.get(panel_url(self.location)).content.decode()
        row = row_html(html, "TCL009-CSRF")
        self.assertIn(f'action="{transition_url(self.location, visit)}"', row)
        self.assertIn('name="csrfmiddlewaretoken"', row)
        self.assertIn(">In Operation</button>", row)


class PanelTransitionTests(PanelTestCase):
    def test_valid_transition_saves_and_redirects_with_a_message(self):
        visit = self.make_visit("TCL009-OK", status=CarrierVisit.ARRIVED)
        response = self.client.post(transition_url(self.location, visit), {"status": "InOperation"})
        self.assertRedirects(response, panel_url(self.location), fetch_redirect_response=False)
        visit.refresh_from_db()
        self.assertEqual(visit.status, CarrierVisit.IN_OPERATION)
        html = self.client.get(panel_url(self.location)).content.decode()
        self.assertIn("Visit TCL009-OK status changed from Arrived to In Operation.", html)
        self.assertEqual(offered_statuses(html, "TCL009-OK"), CarrierVisit.allowed_next_statuses("InOperation"))

    def test_every_forward_step_through_the_panel(self):
        visit = self.make_visit("TCL009-WALK")
        for target in ORDER[1:]:
            response = self.client.post(transition_url(self.location, visit), {"status": target})
            self.assertEqual(response.status_code, 302, (target, response.content[:500]))
            visit.refresh_from_db()
            self.assertEqual(visit.status, target)

    def test_backward_transition_is_refused_with_a_message(self):
        visit = self.make_visit("TCL009-BACK", status=CarrierVisit.ARRIVED)
        response = self.client.post(transition_url(self.location, visit), {"status": "Created"})
        self.assertEqual(response.status_code, 409)
        html = response.content.decode()
        self.assertIn('role="alert"', html)
        self.assertIn("Visit TCL009-BACK cannot go back from Arrived to Created", html)
        visit.refresh_from_db()
        self.assertEqual(visit.status, CarrierVisit.ARRIVED)

    def test_skipped_transition_is_refused_while_the_skip_rule_is_on(self):
        if not CarrierVisit.REFUSE_SKIPPED_STEPS:
            self.skipTest("REFUSE_SKIPPED_STEPS is off (E4): skips are allowed")
        visit = self.make_visit("TCL009-SKIP", status=CarrierVisit.ARRIVED)
        response = self.client.post(transition_url(self.location, visit), {"status": "Departed"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("cannot skip from Arrived to Departed", response.content.decode())
        visit.refresh_from_db()
        self.assertEqual(visit.status, CarrierVisit.ARRIVED)

    def test_unknown_or_missing_status_is_400(self):
        visit = self.make_visit("TCL009-BAD", status=CarrierVisit.ARRIVED)
        response = self.client.post(transition_url(self.location, visit), {"status": "Sunk"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("is not a vessel-visit status", response.content.decode())
        response = self.client.post(transition_url(self.location, visit), {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("No status was chosen.", response.content.decode())
        visit.refresh_from_db()
        self.assertEqual(visit.status, CarrierVisit.ARRIVED)

    def test_same_status_is_a_no_op(self):
        visit = self.make_visit("TCL009-SAME", status=CarrierVisit.ARRIVED)
        before = CarrierVisit.objects.get(pk=visit.pk).last_updated
        response = self.client.post(transition_url(self.location, visit), {"status": "Arrived"}, follow=True)
        self.assertContains(response, "Visit TCL009-SAME is already Arrived; no change.")
        self.assertEqual(CarrierVisit.objects.get(pk=visit.pk).last_updated, before)

    def test_ata_and_atd_are_not_changed_by_a_transition(self):
        # D-025 / PR #27 change the status only (PR #21 Q2 on ata/atd is open).
        ata = datetime(2026, 11, 2, 8, 30, tzinfo=UTC)
        visit = self.make_visit("TCL009-TIMES", status=CarrierVisit.ARRIVED, ata=ata)
        self.client.post(transition_url(self.location, visit), {"status": "InOperation"})
        visit.refresh_from_db()
        self.assertEqual((visit.ata, visit.atd), (ata, None))

    def test_visit_of_another_terminal_or_type_is_404(self):
        other = make_location("TCL-009 Other Terminal")
        foreign = self.make_visit("TCL009-FOREIGN", status=CarrierVisit.ARRIVED, location=other)
        truck = self.make_visit("TCL009-TT", status=CarrierVisit.ARRIVED, visit_type=CarrierVisit.TRUCK)
        for visit in (foreign, truck):
            response = self.client.post(transition_url(self.location, visit), {"status": "InOperation"})
            self.assertEqual(response.status_code, 404)
            visit.refresh_from_db()
            self.assertEqual(visit.status, CarrierVisit.ARRIVED)

    def test_get_is_not_allowed(self):
        visit = self.make_visit("TCL009-GET", status=CarrierVisit.ARRIVED)
        self.assertEqual(self.client.get(transition_url(self.location, visit)).status_code, 405)

    def test_post_without_csrf_token_is_refused(self):
        visit = self.make_visit("TCL009-NOCSRF", status=CarrierVisit.ARRIVED)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(transition_url(self.location, visit), {"status": "InOperation"})
        self.assertEqual(response.status_code, 403)
        visit.refresh_from_db()
        self.assertEqual(visit.status, CarrierVisit.ARRIVED)

    def test_post_with_the_page_csrf_token_is_accepted(self):
        visit = self.make_visit("TCL009-WITHCSRF", status=CarrierVisit.ARRIVED)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        html = client.get(panel_url(self.location)).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', row_html(html, "TCL009-WITHCSRF"))[1]
        response = client.post(
            transition_url(self.location, visit), {"status": "InOperation", "csrfmiddlewaretoken": token},
        )
        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.assertEqual(visit.status, CarrierVisit.IN_OPERATION)


class InOperationHookTests(PanelTestCase):
    """InOperation goes through CarrierVisit.save(), the hook point TCL-010
    discharge planning uses; a refusal from that hook is shown and rolled back."""

    def test_in_operation_is_saved_through_carrier_visit_save(self):
        visit = self.make_visit("TCL009-HOOK", status=CarrierVisit.ARRIVED)
        real_save = CarrierVisit.save
        seen = []

        def recording_save(instance, *args, **kwargs):
            seen.append((instance.pk, instance.status, kwargs.get("update_fields")))
            return real_save(instance, *args, **kwargs)

        with mock.patch.object(CarrierVisit, "save", autospec=True, side_effect=recording_save):
            response = self.client.post(transition_url(self.location, visit), {"status": "InOperation"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(seen, [(visit.pk, CarrierVisit.IN_OPERATION, None)])

    def test_a_refusal_from_the_save_hook_rolls_back_and_is_shown(self):
        # Stand-in for TCL-010's DischargePlanningError (a ValidationError on
        # ``status``) raised after the row was written: the change is undone.
        visit = self.make_visit("TCL009-HOOKFAIL", status=CarrierVisit.ARRIVED)
        real_save = CarrierVisit.save

        def failing_save(instance, *args, **kwargs):
            real_save(instance, *args, **kwargs)
            if instance.status == CarrierVisit.IN_OPERATION:
                raise ValidationError(
                    {"status": ValidationError("Visit TCL009-HOOKFAIL cannot go to InOperation: no layout.")},
                )

        with mock.patch.object(CarrierVisit, "save", autospec=True, side_effect=failing_save):
            response = self.client.post(transition_url(self.location, visit), {"status": "InOperation"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("cannot go to InOperation: no layout.", response.content.decode())
        visit.refresh_from_db()
        self.assertEqual(visit.status, CarrierVisit.ARRIVED)

    def test_other_transitions_do_not_hit_the_hook_path_twice(self):
        visit = self.make_visit("TCL009-ONCE", status=CarrierVisit.PLANNED)
        with mock.patch.object(CarrierVisit, "save", autospec=True) as save:
            self.client.post(transition_url(self.location, visit), {"status": "Announced"})
        self.assertEqual(save.call_count, 1)


class SeededDemoPanelTests(TestCase):
    """Demo script steps 2-4 on the import_v1 seed (D-018: the visit is Arrived)."""

    def setUp(self):
        reset_scenario("import_v1")
        self.visit = CarrierVisit.objects.get()
        self.location = self.visit.location
        self.user = User.objects.create_user(username="tcl009-seed", password="test-password")
        self.client.force_login(self.user)

    def test_seeded_visit_is_arrived_with_its_container_and_offers_in_operation(self):
        html = self.client.get(panel_url(self.location)).content.decode()
        row = row_html(html, self.visit.name)
        self.assertIn('data-status="Arrived"', row)
        self.assertNotIn('<span data-field="ata">—</span>', row)
        self.assertIn("1 inbound", row)
        self.assertEqual(offered_statuses(html, self.visit.name), ["InOperation"])

    def test_demo_refusal_then_in_operation(self):
        url = transition_url(self.location, self.visit)
        refused = self.client.post(url, {"status": "Created"})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("cannot go back from Arrived to Created", refused.content.decode())
        response = self.client.post(url, {"status": "InOperation"})
        self.assertEqual(response.status_code, 302)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.IN_OPERATION)
        # Until TCL-010b the seed pre-creates the discharge order, so the visit
        # has exactly one MOVE_IN order whether or not TCL-010 has landed.
        self.assertEqual(
            Order.objects.filter(container_number__ib_carrier=self.visit, order_type=Order.MOVE_IN).count(), 1,
        )
