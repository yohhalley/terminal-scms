"""TCL-005 consumer-side contract tests for ``scms.execution.event.v1``.

The golden fixtures under ``<repo>/contracts/golden/scms.execution.event.v1/`` are
written by the OES producer test (``apps/terminal-oes/execution/
test_contract_execution_events.py``) from what the real OES outbox publisher puts
on the wire. Here they are fed, unchanged in shape, into the real MiniTOS
consumer ``apply_execution_message``.

Only *id values* are rebound to this test database (order, leg, CHE, position,
site and terminal ids), because OES and MiniTOS run on separate databases. Keys
are never added or removed, so a missing envelope field stays missing.

Set ``SCMS_CONTRACT_STRICT=1`` to turn the known-bug ``expectedFailure`` markers
off and see the real error.
"""

import json
import os
import unittest
from pathlib import Path

from django.test import override_settings
from django.urls import reverse
from rest_framework import status

from minitos_core.models import (
    Che,
    ContainerOwner,
    ContainerUnit,
    ContainerVisit,
    EquipmentType,
    IntegrationInbox,
    ISOType,
    Order,
    OrderLeg,
    OrderRoutePlan,
    Position,
    Site,
    WorkQueue,
)
from minitos_core.services import apply_execution_message, sync_yard_grid
from minitos_core.test_yard_grid import LayoutTestCase

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN_DIR = REPO_ROOT / 'contracts' / 'golden' / 'scms.execution.event.v1'


def known_bug(reason):
    def decorator(func):
        func.__doc__ = f'KNOWN BUG: {reason}\n\n{func.__doc__ or ""}'
        if os.environ.get('SCMS_CONTRACT_STRICT'):
            return func
        return unittest.expectedFailure(func)
    return decorator


def load_golden():
    if not GOLDEN_DIR.is_dir():
        raise unittest.SkipTest(f'{GOLDEN_DIR} is not available (app-only Docker image)')
    files = sorted(GOLDEN_DIR.glob('*.json'))
    if not files:
        raise AssertionError(f'No golden fixtures in {GOLDEN_DIR}')
    return [(path.name, json.loads(path.read_text())) for path in files]


@override_settings(MES_API_TOKEN='test-mes-token')
class GoldenExecutionEventConsumerTests(LayoutTestCase):
    def setUp(self):
        super().setUp()
        self.grid = self.create_grid()
        sync_yard_grid(self.grid)
        self.source = Position.objects.get(
            pile__storage_grid=self.grid, pile__bay=1, pile__row=1, tier=1,
        )
        self.target = Position.objects.get(
            pile__storage_grid=self.grid, pile__bay=1, pile__row=2, tier=1,
        )
        iso_type = ISOType.objects.create(iso_type='22G1')
        owner = ContainerOwner.objects.create(
            name='Contract Line', code='CTRL', is_shippingline=True,
        )
        container = ContainerUnit.objects.create(
            container_number='MSCU1234567', iso_type=iso_type, owner=owner,
            is_in_terminal=True,
        )
        self.visit = ContainerVisit.objects.create(
            location=self.location, container_number=container,
            category=ContainerVisit.STORAGE, status=ContainerVisit.AT_YARD,
        )
        self.source.container_visit = self.visit
        self.source.save()
        self.queue = WorkQueue.objects.create(
            name='YARD-WQ', location=self.location, is_active=True,
        )
        self.order = Order.objects.create(
            order_type=Order.HOUSEKEEPING, status=Order.PLANNED, priority=10,
            container_number=self.visit, work_queue=self.queue,
            from_position=self.source.operational_position,
            to_position=self.target.operational_position,
        )
        plan = OrderRoutePlan.objects.create(
            order=self.order, layout_version=self.layout, version=1,
            status=OrderRoutePlan.ACTIVE,
        )
        self.leg = OrderLeg.objects.create(
            order=self.order, route_plan=plan, sequence=1,
            leg_kind=OrderLeg.HANDLE,
            start_position=self.source.operational_position,
            end_position=self.target.operational_position,
        )
        equipment_type = EquipmentType.objects.create(
            name='Test ASC', equipment_type=EquipmentType.ARMG, is_yard_equipment=True,
        )
        self.che = Che.objects.create(
            name='ASC01', equipment_type=equipment_type, location=self.location,
            powered_on=True, is_available=True,
            latitude=self.source.operational_position.latitude,
            longitude=self.source.operational_position.longitude,
        )
        released = self.client.post(
            reverse(
                'minitos_core:order-release',
                args=[self.location.pk, self.queue.pk, self.order.pk],
            ),
            {}, format='json',
        )
        self.assertEqual(released.status_code, status.HTTP_200_OK, released.data)
        self.site = Site.objects.select_related('enterprise').get(location=self.location)

    def rebind(self, document):
        """Map OES-side ids to this database; never adds or removes keys."""
        document = json.loads(json.dumps(document))
        payload = document['payload']
        data = payload['data']
        mapping = {
            'order_id': self.order.pk,
            'leg_id': self.leg.pk,
            'che_id': self.che.pk,
            'position_id': self.target.pk,
        }
        for key, value in mapping.items():
            if key in data:
                data[key] = value
        if 'site' in payload:
            payload['site']['id'] = str(self.site.pk)
            if 'source_location_id' in payload['site']:
                payload['site']['source_location_id'] = self.location.pk
        if isinstance(payload.get('terminal'), dict) and 'id' in payload['terminal']:
            payload['terminal']['id'] = self.location.pk
        topic = (
            f'scms/v2/{self.site.enterprise.code}/{self.site.code}'
            f'/oes/orders/{self.order.pk}/execution'
        )
        return topic, json.dumps(payload, separators=(',', ':'))

    def test_golden_fixtures_cover_one_full_leg(self):
        events = [doc['payload']['data']['event'] for _, doc in load_golden()]
        self.assertEqual(events, [
            'leg_dispatched', 'leg_started', 'container_picked_up',
            'container_dropped', 'leg_completed',
        ])

    @known_bug(
        "B3/A1: OES-produced envelopes have no 'terminal' key; "
        "apply_execution_message requires payload['terminal']['id'] "
        "(integration_events.py:27-36)."
    )
    def test_golden_envelopes_carry_terminal_id(self):
        for name, document in load_golden():
            self.assertIn('terminal', document['payload'], f"{name}: no 'terminal' key")
            self.assertIn('id', document['payload']['terminal'], f'{name}: no terminal.id')

    @known_bug(
        "B3/A1: the real consumer rejects every OES-produced execution event with "
        "'Execution event requires event_id, terminal.id, order_id, and event.'"
    )
    def test_golden_leg_applies_through_real_consumer(self):
        """Replaying OES's own payloads moves the container and completes the order."""
        golden = load_golden()
        for name, document in golden:
            topic, raw = self.rebind(document)
            try:
                apply_execution_message(topic, raw)
            except Exception as exc:  # report which fixture failed and why
                self.fail(f'{name} rejected by apply_execution_message: {exc}')

        self.order.refresh_from_db()
        self.visit.refresh_from_db()
        self.source.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.order.status, Order.COMPLETE)
        self.assertEqual(self.visit.status, ContainerVisit.AT_YARD)
        self.assertIsNone(self.visit.current_che)
        self.assertIsNone(self.source.container_visit)
        self.assertEqual(self.target.container_visit, self.visit)
        self.assertEqual(
            IntegrationInbox.objects.filter(
                source='terminal-oes', status=IntegrationInbox.PROCESSED,
            ).count(),
            len(golden),
        )
