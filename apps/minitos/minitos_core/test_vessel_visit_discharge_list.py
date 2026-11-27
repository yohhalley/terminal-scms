"""Demo screens S2/S3 (D-041, D-031; TCL-demo-screens).

S2: the read-only discharge list at
``/terminals/<id>/vessel-visits/<visit_id>/discharge-list/``, linked from the
vessel visit panel. S3: ISO number and vessel slot columns on the MiniTOS
admin changelists, and the received time on the CarrierVisit changelist.
Placeholders (board 7 card, waiting on Yael) live in ``demo_display.py`` and
``scenarios/import_v1.py::DISCHARGE_LIST_LEAD_TIME``. QA's AC1.6 acceptance
test comes separately.
"""

import re
from datetime import UTC, datetime, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase
from django.urls import reverse

from minitos_core import demo_display
from minitos_core.models import (
    CarrierVisit, ContainerOwner, ContainerUnit, ContainerVisit, ISOType, LayoutVersion,
    Location, OperationalPosition, Order,
)
from minitos_core.scenarios import import_v1, reset_scenario
from minitos_core.scenarios.base import iso6346_check_digit
from minitos_core.services.vessel_slots import vessel_slot_for, vessel_slots_for

FIELD_RE = r'data-field="{field}"[^>]*>(?P<value>[^<]*)<'


def field_values(html, field):
    return [match['value'] for match in re.finditer(FIELD_RE.format(field=field), html)]


def make_location(name, timezone='UTC'):
    return Location.objects.create(
        name=name, location_type='TL', latitude=41.35, longitude=2.17,
        map_origin_latitude=41.35, map_origin_longitude=2.17, timezone=timezone,
    )


def list_url(location, visit):
    return reverse('vessel_visit_discharge_list', args=[location.pk, visit.pk])


class DemoScreenTestCase(TestCase):
    def setUp(self):
        self.location = make_location('Demo Screens Terminal')
        self.layout = LayoutVersion.objects.create(location=self.location, version=1)
        self.owner = ContainerOwner.objects.create(name='Demo Line', code='MSCU', is_shippingline=True)
        self.iso_type = ISOType.objects.create(iso_type='22G1', length=ISOType.L20, height=ISOType.HNC)
        self.visit = CarrierVisit.objects.create(
            name='DEMO-V1', location=self.location, status=CarrierVisit.ARRIVED,
            vessel_name='TV DEMO', voyage_number='007I',
            ata=datetime(2026, 11, 3, 6, 0, tzinfo=UTC),
            discharge_list_received_at=datetime(2026, 11, 1, 6, 0, tzinfo=UTC),
        )
        self.user = User.objects.create_user(username='demo-screens', password='test-password')
        self.client.force_login(self.user)

    def make_container(self, number, visit=None, **extra):
        unit = ContainerUnit.objects.create(container_number=number, owner=self.owner, iso_type=self.iso_type)
        return ContainerVisit.objects.create(
            container_number=unit, ib_carrier=visit or self.visit, location=self.location,
            category=ContainerVisit.IMPORT, **extra,
        )

    def make_slot(self, container_visit, stow, code=None, layout=None):
        return OperationalPosition.objects.create(
            layout_version=layout or self.layout, code=code or f'DEMO-V1.{stow or container_visit.pk}',
            name='slot', position_kind=OperationalPosition.VESSEL_SLOT,
            logical_address={'carrier_visit': 'DEMO-V1', 'container_visit_id': container_visit.pk, 'stow': stow},
        )


class VesselSlotHelperTests(DemoScreenTestCase):
    def test_real_slot_via_container_visit_id(self):
        visit = self.make_container('MSCU1234566')
        self.make_slot(visit, '0330682')
        self.assertEqual(vessel_slot_for(visit), '0330682')
        self.assertEqual(vessel_slot_for(visit.pk), '0330682')

    def test_planning_placeholder_and_no_slot_are_none(self):
        placeholder = self.make_container('MSCU1234571')
        self.make_slot(placeholder, None, code=f'DEMO-V1.CV{placeholder.pk}')
        bare = self.make_container('MSCU1234587')
        self.assertIsNone(vessel_slot_for(placeholder))
        self.assertIsNone(vessel_slot_for(bare))
        self.assertIsNone(vessel_slot_for(None))

    def test_first_slot_by_code_as_planning_and_bulk_is_two_queries(self):
        first = self.make_container('MSCU1234566')
        second = self.make_container('MSCU1234571')
        self.make_slot(first, None, code=f'DEMO-V1.CV{first.pk}')
        self.make_slot(first, '0330682')
        self.make_slot(second, '0330684')
        with self.assertNumQueries(2):
            slots = vessel_slots_for([first, second])
        self.assertEqual(slots, {first.pk: '0330682', second.pk: '0330684'})
        self.assertEqual(vessel_slots_for([]), {})

    def test_a_placeholder_that_sorts_first_shows_missing_like_planning_uses_it(self):
        visit = self.make_container('MSCU1234566')
        self.make_slot(visit, None, code='A-PLACEHOLDER')
        self.make_slot(visit, '0330682')
        self.assertIsNone(vessel_slot_for(visit))

    def test_only_the_current_layout_of_the_visits_terminal_counts(self):
        # QA review of #54, S4: the same scope as discharge_planning._layout.
        visit = self.make_container('MSCU1234566')
        old_layout = LayoutVersion.objects.create(location=self.location, version=0, is_current=False)
        self.make_slot(visit, '0330682', code='OLD.0330682', layout=old_layout)
        other_location = make_location('Other Slot Terminal')
        other_layout = LayoutVersion.objects.create(location=other_location, version=1)
        self.make_slot(visit, '0330684', code='OTHER.0330684', layout=other_layout)
        self.assertIsNone(vessel_slot_for(visit))
        self.make_slot(visit, '0330686')
        self.assertEqual(vessel_slot_for(visit), '0330686')

    def test_terminal_without_a_current_layout_has_no_slot(self):
        visit = self.make_container('MSCU1234566')
        self.make_slot(visit, '0330682')
        LayoutVersion.objects.filter(pk=self.layout.pk).update(is_current=False)
        self.assertIsNone(vessel_slot_for(visit))

    def test_other_position_kinds_and_malformed_stow_are_ignored(self):
        visit = self.make_container('MSCU1234566')
        OperationalPosition.objects.create(
            layout_version=self.layout, code='Y1', name='yard', position_kind=OperationalPosition.YARD_SLOT,
            logical_address={'container_visit_id': visit.pk, 'stow': '0330682'},
        )
        self.make_slot(visit, '33-06-82')
        self.assertIsNone(vessel_slot_for(visit))


class DischargeListPageTests(DemoScreenTestCase):
    def test_lists_inbound_containers_with_number_and_vessel_slot(self):
        first = self.make_container('MSCU1234566')
        second = self.make_container('MSCU1234571')
        self.make_slot(first, '0330682')
        self.make_slot(second, '0330684')
        other_visit = CarrierVisit.objects.create(name='DEMO-V2', location=self.location)
        self.make_container('MSCU1234587', visit=other_visit)
        response = self.client.get(list_url(self.location, self.visit))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # D-077 stowage order: tier 84 above tier 82 comes first.
        self.assertEqual(field_values(html, 'container-number'), ['MSCU1234571', 'MSCU1234566'])
        self.assertEqual(field_values(html, 'vessel-slot'), ['330684', '330682'])
        self.assertEqual(field_values(html, 'iso-type'), ['22G1', '22G1'])
        self.assertEqual(field_values(html, 'container-count'), ['2'])
        self.assertIn('TV DEMO · 007I', html)

    def test_received_time_and_berthed_in_terminal_time_with_zone_named(self):
        self.location.timezone = 'America/Mexico_City'
        self.location.save()
        html = self.client.get(list_url(self.location, self.visit)).content.decode()
        self.assertEqual(field_values(html, 'discharge-list-received'), ['2026-11-01 00:00'])
        self.assertEqual(field_values(html, 'timezone'), ['America/Mexico_City'] * 2)
        self.assertEqual(field_values(html, 'ata'), ['2026-11-03 00:00'])
        self.assertIn('times in America/Mexico_City', html)
        self.assertIn('Berthed', html)

    def test_missing_values_show_a_dash_and_the_page_renders(self):
        self.visit.discharge_list_received_at = None
        self.visit.ata = None
        self.visit.save()
        placeholder = self.make_container('MSCU1234566')
        self.make_slot(placeholder, None, code=f'DEMO-V1.CV{placeholder.pk}')
        ContainerVisit.objects.create(ib_carrier=self.visit, location=self.location)
        response = self.client.get(list_url(self.location, self.visit))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(field_values(html, 'discharge-list-received'), ['-'])
        self.assertEqual(field_values(html, 'ata'), ['-'])
        self.assertEqual(field_values(html, 'vessel-slot'), ['-', '-'])
        self.assertEqual(field_values(html, 'container-number')[-1], '-')
        self.assertIn('title="no stowage slot"', html)
        self.assertEqual(field_values(html, 'order-status'), ['-', '-'])

    def test_discharge_order_and_yard_slot_are_shown(self):
        container_visit = self.make_container('MSCU1234566')
        vessel_slot = self.make_slot(container_visit, '0330682')
        yard = OperationalPosition.objects.create(
            layout_version=self.layout, code='BLOCK01.01.01.1', name='yard',
            position_kind=OperationalPosition.YARD_SLOT,
        )
        Order.objects.create(
            order_type=Order.MOVE_IN, status=Order.PLANNED, container_number=container_visit,
            from_position=vessel_slot, to_position=yard,
        )
        html = self.client.get(list_url(self.location, self.visit)).content.decode()
        self.assertEqual(field_values(html, 'order-status'), ['Planned'])
        self.assertEqual(field_values(html, 'planned-yard-slot'), ['BLOCK01.01.01.1'])
        self.assertIn('<th>Planned yard slot</th>', html)
        self.assertNotIn('<th>Yard slot</th>', html)
        self.assertEqual(field_values(html, 'vessel-slot'), ['330682'])

    def test_empty_visit(self):
        html = self.client.get(list_url(self.location, self.visit)).content.decode()
        self.assertIn("No inbound containers on this visit's discharge list.", html)

    def test_page_is_read_only(self):
        html = self.client.get(list_url(self.location, self.visit)).content.decode()
        self.assertNotIn('<form', html)
        self.assertEqual(self.client.post(list_url(self.location, self.visit)).status_code, 405)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)

    def test_query_count_does_not_grow_with_containers(self):
        for serial in range(3):
            number = f'MSCU{123456 + serial:06d}'
            self.make_slot(self.make_container(number + str(iso6346_check_digit(number))), f'03306{82 + 2 * serial}')
        with CaptureQueriesContext(connection) as one:
            self.client.get(list_url(self.location, self.visit))
        number = 'MSCU999999'
        self.make_slot(self.make_container(number + str(iso6346_check_digit(number))), '0330690')
        with self.assertNumQueries(len(one.captured_queries)):
            self.client.get(list_url(self.location, self.visit))


class DischargeListAccessTests(DemoScreenTestCase):
    def test_login_required(self):
        self.client.logout()
        response = self.client.get(list_url(self.location, self.visit))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('admin:login'), response['Location'])

    def test_403_without_view_capability(self):
        capabilities = {'can_view_terminal': False, 'can_edit_terminal': False, 'can_manage_berth_plan': False}
        with mock.patch('minitos_core.views.terminal_capabilities', return_value=capabilities):
            self.assertEqual(self.client.get(list_url(self.location, self.visit)).status_code, 403)

    def test_view_capability_is_enough_for_the_read_only_page(self):
        # TL call (b) on QA review of #54, N1: can_view_terminal, not can_manage_berth_plan.
        capabilities = {'can_view_terminal': True, 'can_edit_terminal': False, 'can_manage_berth_plan': False}
        with mock.patch('minitos_core.views.terminal_capabilities', return_value=capabilities):
            self.assertEqual(self.client.get(list_url(self.location, self.visit)).status_code, 200)
            # The panel (it changes statuses) still needs can_manage_berth_plan.
            panel = self.client.get(reverse('vessel_visit_panel', args=[self.location.pk]))
            self.assertEqual(panel.status_code, 403)

    def test_head_is_allowed(self):
        self.assertEqual(self.client.head(list_url(self.location, self.visit)).status_code, 200)

    def test_404_for_other_terminal_non_vessel_or_unknown_visit(self):
        other = make_location('Other Terminal')
        foreign = CarrierVisit.objects.create(name='DEMO-FOREIGN', location=other)
        truck = CarrierVisit.objects.create(name='DEMO-TRUCK', location=self.location, visit_type=CarrierVisit.TRUCK)
        self.assertEqual(self.client.get(list_url(self.location, foreign)).status_code, 404)
        self.assertEqual(self.client.get(list_url(self.location, truck)).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('vessel_visit_discharge_list', args=[self.location.pk, 999999])).status_code, 404,
        )
        self.assertEqual(
            self.client.get(reverse('vessel_visit_discharge_list', args=[999999, self.visit.pk])).status_code, 404,
        )

    def test_404_for_non_terminal_location_type(self):
        # QA review of #54, N2: a location whose location_type is not TL/ID is 404.
        vessel = Location.objects.create(
            name='Not A Terminal', location_type=Location.VL,
            latitude=41.35, longitude=2.17,
            map_origin_latitude=41.35, map_origin_longitude=2.17,
        )
        visit = CarrierVisit.objects.create(name='DEMO-ON-VL', location=vessel)
        self.assertEqual(self.client.get(list_url(vessel, visit)).status_code, 404)

    def test_unknown_location_is_404_before_capability_403(self):
        # QA review of #54, N1: get_object_or_404 runs first, so an unknown
        # location is 404 even without the view capability; another terminal is 403.
        capabilities = {'can_view_terminal': False, 'can_edit_terminal': False, 'can_manage_berth_plan': False}
        other = make_location('Other Access Terminal')
        foreign = CarrierVisit.objects.create(name='DEMO-FOREIGN-ACCESS', location=other)
        with mock.patch('minitos_core.views.terminal_capabilities', return_value=capabilities):
            self.assertEqual(
                self.client.get(
                    reverse('vessel_visit_discharge_list', args=[999999, self.visit.pk])
                ).status_code,
                404,
            )
            self.assertEqual(self.client.get(list_url(other, foreign)).status_code, 403)


class PanelDischargeListTests(DemoScreenTestCase):
    def test_panel_links_to_the_discharge_list_and_shows_the_received_time(self):
        self.make_container('MSCU1234566')
        html = self.client.get(reverse('vessel_visit_panel', args=[self.location.pk])).content.decode()
        self.assertIn(f'href="{list_url(self.location, self.visit)}">Discharge list (1)</a>', html)
        self.assertEqual(field_values(html, 'discharge-list-received'), ['2026-11-01 06:00'])
        # SME pre-merge #54 point 7: same display label as S2 (D-024: ata = berthed).
        self.assertIn('<b>Berthed</b>', html)
        self.assertNotIn('<b>ATA</b>', html)

    def test_panel_shows_a_dash_without_a_received_time(self):
        self.visit.discharge_list_received_at = None
        self.visit.save()
        html = self.client.get(reverse('vessel_visit_panel', args=[self.location.pk])).content.decode()
        self.assertEqual(field_values(html, 'discharge-list-received'), ['-'])

    def test_panel_received_time_in_a_non_utc_terminal(self):
        self.location.timezone = 'America/Mexico_City'
        self.location.save()
        html = self.client.get(reverse('vessel_visit_panel', args=[self.location.pk])).content.decode()
        self.assertEqual(field_values(html, 'discharge-list-received'), ['2026-11-01 00:00'])
        self.assertIn('times in America/Mexico_City', html)


class PlaceholderConstantTests(TestCase):
    """Each board-7 answer is a one-constant change in demo_display.py."""

    def test_defaults(self):
        self.assertEqual(demo_display.format_container_number('MSCU1234566'), 'MSCU1234566')
        self.assertEqual(demo_display.format_vessel_slot('0330682'), '330682')
        self.assertEqual(demo_display.format_vessel_slot(None), '-')
        self.assertEqual(demo_display.format_container_number(None), '-')
        self.assertEqual(import_v1.DISCHARGE_LIST_LEAD_TIME, timedelta(hours=48))

    def test_alternatives(self):
        with mock.patch.object(demo_display, 'CONTAINER_NUMBER_FORMAT', 'spaced'):
            self.assertEqual(demo_display.format_container_number('MSCU1234566'), 'MSCU 123456 6')
        with mock.patch.object(demo_display, 'VESSEL_SLOT_FORMAT', 'spelled'):
            self.assertEqual(demo_display.format_vessel_slot('0330682'), 'Bay 33 · Row 06 · Tier 82')
        location = Location(name='x', timezone='America/Mexico_City')
        value = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
        self.assertEqual(
            demo_display.format_local_time(value, location, with_zone=True), '2026-10-22 18:00 America/Mexico_City',
        )
        with mock.patch.object(demo_display, 'TIME_DISPLAY_ZONE', 'UTC'):
            self.assertEqual(demo_display.format_local_time(value, location, with_zone=True), '2026-10-23 00:00 UTC')
        with mock.patch.object(demo_display, 'MISSING_VALUE', 'n/a'):
            self.assertEqual(demo_display.format_vessel_slot(None), 'n/a')


class PlaceholderSwitchRenderTests(DemoScreenTestCase):
    """QA review of #54, S1: TIME_FORMAT and MISSING_VALUE drive every time cell."""

    def setUp(self):
        super().setUp()
        self.visit.eta = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
        self.visit.save()
        self.make_container('MSCU1234566')

    def panel_html(self):
        return self.client.get(reverse('vessel_visit_panel', args=[self.location.pk])).content.decode()

    def test_time_format_drives_s2_and_every_panel_time(self):
        with mock.patch.object(demo_display, 'TIME_FORMAT', '%d/%m/%Y %H:%M'):
            s2 = self.client.get(list_url(self.location, self.visit)).content.decode()
            panel = self.panel_html()
        self.assertEqual(field_values(s2, 'discharge-list-received'), ['01/11/2026 06:00'])
        self.assertEqual(field_values(s2, 'ata'), ['03/11/2026 06:00'])
        self.assertEqual(field_values(panel, 'ata'), ['03/11/2026 06:00'])
        self.assertEqual(field_values(panel, 'discharge-list-received'), ['01/11/2026 06:00'])
        self.assertIn('<b>ETA</b> 03/11/2026 05:00', panel)
        self.assertNotIn('2026-11-0', s2 + panel)

    def test_missing_value_drives_s2_and_every_panel_cell(self):
        self.visit.eta = self.visit.ata = self.visit.discharge_list_received_at = None
        self.visit.save()
        with mock.patch.object(demo_display, 'MISSING_VALUE', 'n/a'):
            s2 = self.client.get(list_url(self.location, self.visit)).content.decode()
            panel = self.panel_html()
        self.assertEqual(field_values(s2, 'discharge-list-received'), ['n/a'])
        self.assertEqual(field_values(s2, 'ata'), ['n/a'])
        self.assertEqual(field_values(s2, 'vessel-slot'), ['n/a'])
        self.assertEqual(field_values(s2, 'planned-yard-slot'), ['n/a'])
        self.assertEqual(field_values(panel, 'ata'), ['n/a'])
        self.assertEqual(field_values(panel, 'atd'), ['n/a'])
        self.assertEqual(field_values(panel, 'discharge-list-received'), ['n/a'])
        self.assertIn('<b>ETA</b> n/a', panel)
        self.assertIn('<b>ETD</b> n/a', panel)
        self.assertIsNone(re.search(r'>\s*-\s*<', s2 + panel))


class AdminChangelistTests(DemoScreenTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser(username='demo-admin', password='test-password')
        self.client.force_login(self.admin)
        self.container_visit = self.make_container('MSCU1234566')
        self.slot = self.make_slot(self.container_visit, '0330682')
        self.bare = self.make_container('MSCU1234571')

    def test_container_visit_changelist_shows_number_and_vessel_slot(self):
        response = self.client.get(reverse('admin:minitos_core_containervisit_changelist'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Container number', html)
        self.assertIn('Vessel slot', html)
        self.assertIn('<td class="field-vessel_slot">330682</td>', html)
        self.assertIn('<td class="field-vessel_slot">-</td>', html)
        self.assertIn('<td class="field-container_number_display">MSCU1234571</td>', html)
        self.assertIn('<td class="field-status_display">advised, on inbound vessel</td>', html)

    def test_changelist_slot_queries_do_not_grow_with_rows(self):
        # QA review of #54, N4: one bulk slot lookup for the page; must not
        # fall back to vessel_slot_for per row as the changelist grows.
        cv_url = reverse('admin:minitos_core_containervisit_changelist')
        order_url = reverse('admin:minitos_core_order_changelist')
        Order.objects.create(
            order_type=Order.MOVE_IN, container_number=self.container_visit, from_position=self.slot,
        )
        self.client.get(cv_url)
        self.client.get(order_url)
        with CaptureQueriesContext(connection) as cv_one:
            self.assertEqual(self.client.get(cv_url).status_code, 200)
        with CaptureQueriesContext(connection) as order_one:
            self.assertEqual(self.client.get(order_url).status_code, 200)
        for serial in range(3):
            number = f'MSCU{223456 + serial:06d}'
            full = number + str(iso6346_check_digit(number))
            visit = self.make_container(full)
            stow = f'03506{82 + 2 * serial}'
            slot = self.make_slot(visit, stow, code=f'DEMO-V1.EXTRA.{stow}')
            Order.objects.create(order_type=Order.MOVE_IN, container_number=visit, from_position=slot)
        with self.assertNumQueries(len(cv_one.captured_queries)):
            self.client.get(cv_url)
        with self.assertNumQueries(len(order_one.captured_queries)):
            self.client.get(order_url)

    def test_order_changelist_shows_number_and_vessel_slot(self):
        Order.objects.create(order_type=Order.MOVE_IN, container_number=self.container_visit, from_position=self.slot)
        Order.objects.create(order_type=Order.HOUSEKEEPING)
        html = self.client.get(reverse('admin:minitos_core_order_changelist')).content.decode()
        self.assertIn('<td class="field-container_number_display">MSCU1234566</td>', html)
        self.assertIn('<td class="field-vessel_slot">330682</td>', html)
        self.assertIn('<td class="field-container_number_display">-</td>', html)

    def test_carrier_visit_changelist_shows_the_received_time_with_zone(self):
        self.location.timezone = 'America/Mexico_City'
        self.location.save()
        html = self.client.get(reverse('admin:minitos_core_carriervisit_changelist')).content.decode()
        self.assertIn('<td class="field-discharge_list_received">2026-11-01 00:00 America/Mexico_City</td>', html)
        # QA review of #54, S2: ata in the same display zone and format.
        self.assertIn('<td class="field-ata_display">2026-11-03 00:00 America/Mexico_City</td>', html)
        self.assertIn('Berthed (ATA)', html)
        self.assertNotIn('field-ata"', html)

    def test_carrier_visit_changelist_missing_ata(self):
        self.visit.ata = None
        self.visit.save()
        html = self.client.get(reverse('admin:minitos_core_carriervisit_changelist')).content.decode()
        self.assertIn('<td class="field-ata_display">-</td>', html)

    def test_change_forms_still_render(self):
        for name, obj in (('containervisit', self.container_visit), ('carriervisit', self.visit)):
            url = reverse(f'admin:minitos_core_{name}_change', args=[obj.pk])
            self.assertEqual(self.client.get(url).status_code, 200)


class SeededDischargeListTests(TestCase):
    """The import_v1 seed (TCL-010b layout) on S2 and S3, before and after InOperation."""

    def setUp(self):
        reset_scenario('import_v1')
        self.visit = CarrierVisit.objects.get()
        self.location = self.visit.location
        self.user = User.objects.create_superuser(username='demo-seed', password='test-password')
        self.client.force_login(self.user)

    def test_seeded_received_time_is_48h_before_eta(self):
        # TL, 23 Nov 2026: the lead counts from ETA (planning view); ATA = ETA in the seed.
        self.assertEqual(self.visit.discharge_list_received_at, self.visit.eta - import_v1.DISCHARGE_LIST_LEAD_TIME)
        self.assertEqual(self.visit.eta, import_v1.VESSEL_ETA)
        self.assertEqual(self.visit.ata, import_v1.SCENARIO_EPOCH)
        self.assertEqual(self.visit.discharge_list_received_at, import_v1.SCENARIO_EPOCH - timedelta(hours=48))
        self.assertLess(self.visit.discharge_list_received_at, self.visit.ata)

    def test_seeded_discharge_list_before_and_after_in_operation(self):
        url = list_url(self.location, self.visit)
        html = self.client.get(url).content.decode()
        numbers = field_values(html, 'container-number')
        self.assertEqual(numbers, ['MSCU1234566'])
        for number in numbers:
            self.assertEqual(str(iso6346_check_digit(number[:10])), number[10])
        self.assertEqual(field_values(html, 'vessel-slot'), ['330682'])
        # 2026-10-25 00:00 UTC - 48 h, in America/Mexico_City (UTC-6).
        self.assertEqual(field_values(html, 'discharge-list-received'), ['2026-10-22 18:00'])
        self.assertEqual(field_values(html, 'timezone'), ['America/Mexico_City'] * 2)
        self.assertEqual(field_values(html, 'order-status'), ['-'])
        self.assertEqual(field_values(html, 'planned-yard-slot'), ['-'])
        seeded_slot = OperationalPosition.objects.get(position_kind=OperationalPosition.VESSEL_SLOT)
        self.assertEqual(seeded_slot.code, 'V001.0330682')
        self.assertEqual(seeded_slot.logical_address['stow'], '0330682')
        self.visit.status = CarrierVisit.IN_OPERATION
        self.visit.save()
        html = self.client.get(url).content.decode()
        order = Order.objects.get()
        # QA review of #54, S5: exact values, one row.
        self.assertEqual(len(field_values(html, 'container-number')), 1)
        self.assertEqual(field_values(html, 'container-number'), ['MSCU1234566'])
        self.assertEqual(field_values(html, 'status'), ['advised, on inbound vessel'])
        self.assertEqual(field_values(html, 'order-status'), ['Planned'])
        self.assertEqual(field_values(html, 'planned-yard-slot'), [order.to_position.code])
        # S4: S2 shows the slot planning used as the order's origin, V001.0330682.
        self.assertEqual(order.from_position.code, 'V001.0330682')
        self.assertEqual(
            field_values(html, 'vessel-slot'),
            [demo_display.format_vessel_slot(order.from_position.logical_address['stow'])],
        )
        self.assertEqual(field_values(html, 'vessel-slot'), ['330682'])

    def test_seeded_admin_changelists(self):
        html = self.client.get(reverse('admin:minitos_core_containervisit_changelist')).content.decode()
        self.assertIn('<td class="field-container_number_display">MSCU1234566</td>', html)
        self.assertIn('<td class="field-vessel_slot">330682</td>', html)
        self.assertIn('<td class="field-status_display">advised, on inbound vessel</td>', html)
        self.visit.status = CarrierVisit.IN_OPERATION
        self.visit.save()
        html = self.client.get(reverse('admin:minitos_core_order_changelist')).content.decode()
        self.assertIn('<td class="field-vessel_slot">330682</td>', html)


class D032StatusLabelTests(TestCase):
    """D-032: display label only; the stored enum value stays Preadvised."""

    def test_display_label_and_stored_value(self):
        self.assertEqual(ContainerVisit.PREADVISED, 'Preadvised')
        visit = ContainerVisit(status=ContainerVisit.PREADVISED)
        self.assertEqual(visit.get_status_display(), 'advised, on inbound vessel')
        visit.status = ContainerVisit.ARRIVED
        self.assertEqual(visit.get_status_display(), 'Arrived')
        self.assertEqual(visit.status, ContainerVisit.ARRIVED)


class D077DecisionTests(DemoScreenTestCase):
    """D-077 (Yael's answers on the demo-screens card), one test per rule."""

    def s2(self):
        return self.client.get(list_url(self.location, self.visit)).content.decode()

    def test_times_are_terminal_local_and_every_time_is_labelled_with_the_zone(self):
        self.location.timezone = 'America/Mexico_City'
        self.location.save()
        self.visit.eta = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
        self.visit.etd = self.visit.atd = None
        self.visit.save()
        s2 = self.s2()
        self.assertEqual(field_values(s2, 'discharge-list-received'), ['2026-11-01 00:00'])
        self.assertEqual(field_values(s2, 'timezone'), ['America/Mexico_City', 'America/Mexico_City'])
        self.assertIn('times in America/Mexico_City (terminal local time)', s2)
        panel = self.client.get(reverse('vessel_visit_panel', args=[self.location.pk])).content.decode()
        # ETA, ETD, Berthed, ATD and the received time each carry the zone.
        self.assertEqual(field_values(panel, 'timezone'), ['America/Mexico_City'] * 5)
        self.assertIn('<b>ETA</b> 2026-11-02 23:00 <span class="zone" data-field="timezone">America/Mexico_City</span>', panel)
        self.assertIn('times in America/Mexico_City (terminal local time)', panel)

    def test_container_number_is_one_string(self):
        self.make_container('MSCU1234566')
        self.assertEqual(demo_display.CONTAINER_NUMBER_FORMAT, 'plain')
        self.assertEqual(field_values(self.s2(), 'container-number'), ['MSCU1234566'])

    def test_vessel_slot_shows_6_characters_bay_above_99_keeps_3(self):
        fmt = demo_display.format_vessel_slot
        self.assertEqual(fmt('0330682'), '330682')
        self.assertEqual(fmt('0010282'), '010282')
        self.assertEqual(fmt('0990682'), '990682')
        self.assertEqual(fmt('1010682'), '1010682')
        self.assertEqual(fmt('33-06-82'), '33-06-82')
        visit = self.make_container('MSCU1234566')
        position = self.make_slot(visit, '0330682')
        self.assertEqual(field_values(self.s2(), 'vessel-slot'), ['330682'])
        position.refresh_from_db()
        self.assertEqual(position.logical_address['stow'], '0330682')  # stored code unchanged

    def test_vessel_slot_is_compact_bay_row_tier(self):
        self.make_slot(self.make_container('MSCU1234566'), '0330682')
        self.assertEqual(demo_display.VESSEL_SLOT_FORMAT, 'compact')
        self.assertEqual(field_values(self.s2(), 'vessel-slot'), ['330682'])

    def test_missing_info_is_a_hyphen(self):
        self.assertEqual(demo_display.MISSING_VALUE, '-')
        self.visit.discharge_list_received_at = None
        self.visit.save()
        ContainerVisit.objects.create(ib_carrier=self.visit, location=self.location)
        html = self.s2()
        for field in ('discharge-list-received', 'container-number', 'vessel-slot', 'weight', 'order-status'):
            self.assertEqual(field_values(html, field), ['-'], field)
        self.assertNotIn('—', html)

    def test_weight_column_in_tonnes(self):
        self.make_container('MSCU1234566', weight=18400)
        self.assertEqual(field_values(self.s2(), 'weight'), ['18.4 t'])
        html = self.s2()
        # SME BL-54e: labelled as an estimate, with a tooltip; never VGM.
        self.assertIn(
            '<th title="Scenario default, not a declared or verified weight: an empty box shows its tare, '
            'a full box a seeded default gross weight." data-field="weight-header">Weight (t, est.)</th>',
            html,
        )
        self.assertNotIn('<th>Weight</th>', html)
        self.assertNotIn('VGM', html)

    def test_rows_in_stowage_order_deck_before_hold_top_tier_first(self):
        cases = [
            ('MSCU1234566', '0350684'),  # bay 35 deck
            ('MSCU1234571', '0330204'),  # bay 33 hold tier 04
            ('MSCU1234587', '0330682'),  # bay 33 deck tier 82
            ('MSCU1234592', '0330686'),  # bay 33 deck tier 86
            ('MSCU1234608', '0330206'),  # bay 33 hold tier 06
            ('MSCU1234613', None),       # no slot: last
        ]
        for number, stow in cases:
            visit = self.make_container(number)
            if stow:
                self.make_slot(visit, stow)
        self.assertEqual(
            field_values(self.s2(), 'vessel-slot'), ['330686', '330682', '330206', '330204', '350684', '-'],
        )

    def test_row_quay_distance_port_and_starboard(self):
        rank = demo_display.row_quay_distance
        rows = [0, 1, 2, 3, 4, 5, 6]
        self.assertEqual(demo_display.QUAY_SIDE, 'port')
        # Port side alongside: outermost port row first, outermost starboard row last.
        self.assertEqual(sorted(rows, key=rank), [6, 4, 2, 0, 1, 3, 5])
        self.assertEqual(sorted(rows, key=lambda row: rank(row, 'starboard')), [5, 3, 1, 0, 2, 4, 6])
        with mock.patch.object(demo_display, 'QUAY_SIDE', 'starboard'):
            self.assertEqual(sorted(rows, key=rank), [5, 3, 1, 0, 2, 4, 6])

    def test_rows_within_a_tier_nearest_the_quay_first(self):
        # D-079: top tier first, then nearest the quay, then the tier below.
        for number, stow in [
            ('MSCU1234566', '0330182'), ('MSCU1234571', '0330082'), ('MSCU1234587', '0330282'),
            ('MSCU1234592', '0330684'), ('MSCU1234608', '0330482'), ('MSCU1234613', '0330584'),
        ]:
            self.make_slot(self.make_container(number), stow)
        self.assertEqual(
            field_values(self.s2(), 'vessel-slot'), ['330684', '330584', '330482', '330282', '330082', '330182'],
        )
        with mock.patch.object(demo_display, 'QUAY_SIDE', 'starboard'):
            self.assertEqual(
                field_values(self.s2(), 'vessel-slot'), ['330584', '330684', '330182', '330082', '330282', '330482'],
            )

    def test_stowage_sort_key(self):
        key = demo_display.stowage_sort_key
        self.assertLess(key('0330682'), key('0330206'))  # deck before hold
        self.assertLess(key('0330684'), key('0330682'))  # top tier first
        self.assertLess(key('0330482'), key('0330282'))  # then the row nearest the quay (port default)
        self.assertLess(key('0350206'), key(None))
        self.assertEqual(key('bad'), key(None))

    def test_wrong_check_digit_warns_on_s2_but_is_shown(self):
        self.make_container('MSCU1234565')  # valid digit is 6
        self.make_container('MSCU1234566')
        response = self.client.get(list_url(self.location, self.visit))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertEqual(field_values(html, 'container-number'), ['MSCU1234565', 'MSCU1234566'])
        self.assertEqual(
            field_values(html, 'check-digit-warning'), ['ISO 6346 check digit may be wrong (expected 6).'],
        )

    def test_check_digit_warning_helper(self):
        warning = demo_display.check_digit_warning
        self.assertIsNone(warning('MSCU1234566'))
        self.assertIsNone(warning(None))
        self.assertIn('expected 6', warning('MSCU1234565'))
        self.assertIn('not 4 letters and 7 digits', warning('MSCU12345'))

    def test_admin_entry_with_a_wrong_check_digit_saves_with_a_warning(self):
        admin_user = User.objects.create_superuser(username='d077-admin', password='test-password')
        self.client.force_login(admin_user)
        response = self.client.post(reverse('admin:minitos_core_containerunit_add'), {
            'container_number': 'MSCU7654320', 'owner': self.owner.pk, 'iso_type': self.iso_type.pk,
            'is_active': 'on',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ContainerUnit.objects.filter(pk='MSCU7654320').exists())
        messages = [str(message) for message in response.context['messages']]
        self.assertTrue(any('check digit may be wrong' in message for message in messages), messages)
        changelist = self.client.get(reverse('admin:minitos_core_containerunit_changelist')).content.decode()
        self.assertIn('check digit may be wrong', changelist)

    def test_admin_entry_with_a_valid_check_digit_has_no_warning(self):
        admin_user = User.objects.create_superuser(username='d077-admin2', password='test-password')
        self.client.force_login(admin_user)
        response = self.client.post(reverse('admin:minitos_core_containerunit_add'), {
            'container_number': 'MSCU1234566', 'owner': self.owner.pk, 'iso_type': self.iso_type.pk,
            'is_active': 'on',
        }, follow=True)
        messages = [str(message) for message in response.context['messages']]
        self.assertFalse(any('check digit' in message for message in messages), messages)

    def test_container_visit_changelist_warns_on_a_wrong_check_digit(self):
        admin_user = User.objects.create_superuser(username='d077-admin3', password='test-password')
        self.client.force_login(admin_user)
        self.make_container('MSCU1234565')
        html = self.client.get(reverse('admin:minitos_core_containervisit_changelist')).content.decode()
        self.assertIn('<td class="field-check_digit">ISO 6346 check digit may be wrong (expected 6).</td>', html)


class D077SeedWeightTests(TestCase):
    """D-077 Q6 default weights in the import_v1 seed (deterministic RNG)."""

    def test_default_weight_rules(self):
        import random as random_module
        rng = random_module.Random(1)
        self.assertEqual(import_v1.default_weight_kg(ISOType.L20, full=False, rng=rng), 2000)
        self.assertEqual(import_v1.default_weight_kg(ISOType.L40, full=False, rng=rng), 4000)
        self.assertEqual(import_v1.default_weight_kg(ISOType.L45, full=False, rng=rng), 4000)
        self.assertEqual(rng.getstate(), random_module.Random(1).getstate())  # empties draw nothing
        draws = [import_v1.default_weight_kg(ISOType.L20, full=True, rng=rng) for _ in range(200)]
        self.assertTrue(all(10000 <= weight <= 25000 for weight in draws))
        self.assertEqual(draws, [
            import_v1.default_weight_kg(ISOType.L20, full=True, rng=rng2)
            for rng2 in [random_module.Random(1)] for _ in range(200)
        ])

    def test_seeded_fcl_weights_are_10_to_25_t_and_stable(self):
        first = [visit.weight for visit in reset_scenario('import_v1', containers=5)['container_visits']]
        again = [visit.weight for visit in reset_scenario('import_v1', containers=5)['container_visits']]
        self.assertEqual(first, again)
        self.assertTrue(all(10000 <= weight <= 25000 for weight in first), first)

    def test_seeded_rows_in_stowage_order(self):
        reset_scenario('import_v1', containers=3)
        visit = CarrierVisit.objects.get()
        user = User.objects.create_user(username='d077-seed', password='test-password')
        self.client.force_login(user)
        html = self.client.get(list_url(visit.location, visit)).content.decode()
        # One row, tiers 82, 84, 86 on deck: top tier first.
        self.assertEqual(field_values(html, 'vessel-slot'), ['330686', '330684', '330682'])
        self.assertTrue(all(value.endswith(' t') for value in field_values(html, 'weight')))
