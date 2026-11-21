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

    def make_slot(self, container_visit, stow, code=None):
        return OperationalPosition.objects.create(
            layout_version=self.layout, code=code or f'DEMO-V1.{stow or container_visit.pk}',
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

    def test_real_slot_wins_over_a_placeholder_and_bulk_is_one_query(self):
        first = self.make_container('MSCU1234566')
        second = self.make_container('MSCU1234571')
        self.make_slot(first, None, code=f'DEMO-V1.CV{first.pk}')
        self.make_slot(first, '0330682')
        self.make_slot(second, '0330684')
        with self.assertNumQueries(1):
            slots = vessel_slots_for([first.pk, second.pk, 999999])
        self.assertEqual(slots, {first.pk: '0330682', second.pk: '0330684', 999999: None})

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
        self.assertEqual(field_values(html, 'container-number'), ['MSCU1234566', 'MSCU1234571'])
        self.assertEqual(field_values(html, 'vessel-slot'), ['0330682', '0330684'])
        self.assertEqual(field_values(html, 'iso-type'), ['22G1', '22G1'])
        self.assertEqual(field_values(html, 'container-count'), ['2'])
        self.assertIn('TV DEMO · 007I', html)

    def test_received_time_and_berthed_in_terminal_time_with_zone_named(self):
        self.location.timezone = 'America/Mexico_City'
        self.location.save()
        html = self.client.get(list_url(self.location, self.visit)).content.decode()
        self.assertEqual(field_values(html, 'discharge-list-received'), ['2026-11-01 00:00'])
        self.assertEqual(field_values(html, 'timezone'), ['America/Mexico_City'])
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
        self.assertEqual(field_values(html, 'discharge-list-received'), ['—'])
        self.assertEqual(field_values(html, 'ata'), ['—'])
        self.assertEqual(field_values(html, 'vessel-slot'), ['—', '—'])
        self.assertEqual(field_values(html, 'container-number')[-1], '—')
        self.assertIn('title="no stowage slot"', html)
        self.assertEqual(field_values(html, 'order-status'), ['—', '—'])

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
        self.assertEqual(field_values(html, 'yard-slot'), ['BLOCK01.01.01.1'])
        self.assertEqual(field_values(html, 'vessel-slot'), ['0330682'])

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

    def test_403_without_berth_plan_capability(self):
        capabilities = {'can_view_terminal': True, 'can_edit_terminal': False, 'can_manage_berth_plan': False}
        with mock.patch('minitos_core.views.terminal_capabilities', return_value=capabilities):
            self.assertEqual(self.client.get(list_url(self.location, self.visit)).status_code, 403)

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


class PanelDischargeListTests(DemoScreenTestCase):
    def test_panel_links_to_the_discharge_list_and_shows_the_received_time(self):
        self.make_container('MSCU1234566')
        html = self.client.get(reverse('vessel_visit_panel', args=[self.location.pk])).content.decode()
        self.assertIn(f'href="{list_url(self.location, self.visit)}">Discharge list (1)</a>', html)
        self.assertEqual(field_values(html, 'discharge-list-received'), ['2026-11-01 06:00'])

    def test_panel_shows_a_dash_without_a_received_time(self):
        self.visit.discharge_list_received_at = None
        self.visit.save()
        html = self.client.get(reverse('vessel_visit_panel', args=[self.location.pk])).content.decode()
        self.assertEqual(field_values(html, 'discharge-list-received'), ['—'])


class PlaceholderConstantTests(TestCase):
    """Each board-7 answer is a one-constant change in demo_display.py."""

    def test_defaults(self):
        self.assertEqual(demo_display.format_container_number('MSCU1234566'), 'MSCU1234566')
        self.assertEqual(demo_display.format_vessel_slot('0330682'), '0330682')
        self.assertEqual(demo_display.format_vessel_slot(None), '—')
        self.assertEqual(demo_display.format_container_number(None), '—')
        self.assertEqual(import_v1.DISCHARGE_LIST_LEAD_TIME, timedelta(hours=48))

    def test_alternatives(self):
        with mock.patch.object(demo_display, 'CONTAINER_NUMBER_FORMAT', 'spaced'):
            self.assertEqual(demo_display.format_container_number('MSCU1234566'), 'MSCU 123456 6')
        with mock.patch.object(demo_display, 'VESSEL_SLOT_FORMAT', 'spelled'):
            self.assertEqual(demo_display.format_vessel_slot('0330682'), 'Bay 033 · Row 06 · Tier 82')
        location = Location(name='x', timezone='America/Mexico_City')
        value = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
        self.assertEqual(
            demo_display.format_local_time(value, location, with_zone=True), '2026-10-22 18:00 America/Mexico_City',
        )
        with mock.patch.object(demo_display, 'TIME_DISPLAY_ZONE', 'UTC'):
            self.assertEqual(demo_display.format_local_time(value, location, with_zone=True), '2026-10-23 00:00 UTC')
        with mock.patch.object(demo_display, 'MISSING_VALUE', 'n/a'):
            self.assertEqual(demo_display.format_vessel_slot(None), 'n/a')


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
        self.assertIn('<td class="field-vessel_slot">0330682</td>', html)
        self.assertIn('<td class="field-vessel_slot">—</td>', html)
        self.assertIn('<td class="field-container_number_display">MSCU1234571</td>', html)

    def test_order_changelist_shows_number_and_vessel_slot(self):
        Order.objects.create(order_type=Order.MOVE_IN, container_number=self.container_visit, from_position=self.slot)
        Order.objects.create(order_type=Order.HOUSEKEEPING)
        html = self.client.get(reverse('admin:minitos_core_order_changelist')).content.decode()
        self.assertIn('<td class="field-container_number_display">MSCU1234566</td>', html)
        self.assertIn('<td class="field-vessel_slot">0330682</td>', html)
        self.assertIn('<td class="field-container_number_display">—</td>', html)

    def test_carrier_visit_changelist_shows_the_received_time_with_zone(self):
        self.location.timezone = 'America/Mexico_City'
        self.location.save()
        html = self.client.get(reverse('admin:minitos_core_carriervisit_changelist')).content.decode()
        self.assertIn('<td class="field-discharge_list_received">2026-11-01 00:00 America/Mexico_City</td>', html)

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

    def test_seeded_received_time_is_48h_before_berthing(self):
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
        self.assertEqual(field_values(html, 'vessel-slot'), ['0330682'])
        # 2026-10-25 00:00 UTC - 48 h, in America/Mexico_City (UTC-6).
        self.assertEqual(field_values(html, 'discharge-list-received'), ['2026-10-22 18:00'])
        self.assertEqual(field_values(html, 'timezone'), ['America/Mexico_City'])
        self.assertEqual(field_values(html, 'order-status'), ['—'])
        self.visit.status = CarrierVisit.IN_OPERATION
        self.visit.save()
        html = self.client.get(url).content.decode()
        self.assertEqual(field_values(html, 'vessel-slot'), ['0330682'])
        self.assertNotEqual(field_values(html, 'order-status'), ['—'])
        self.assertNotEqual(field_values(html, 'yard-slot'), ['—'])

    def test_seeded_admin_changelists(self):
        html = self.client.get(reverse('admin:minitos_core_containervisit_changelist')).content.decode()
        self.assertIn('<td class="field-container_number_display">MSCU1234566</td>', html)
        self.assertIn('<td class="field-vessel_slot">0330682</td>', html)
        self.visit.status = CarrierVisit.IN_OPERATION
        self.visit.save()
        html = self.client.get(reverse('admin:minitos_core_order_changelist')).content.decode()
        self.assertIn('<td class="field-vessel_slot">0330682</td>', html)
