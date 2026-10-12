import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    DispatchAssignment,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    InboxEvent,
    OutboxEvent,
)
from .services.dispatch import apply_command_event, dispatch_next_operation
from .services.ingestion import ingest_equipment_state, ingest_released_order


class ExecutionServiceTests(TestCase):
    def released_order_payload(self, event_id=None):
        return {
            "schema": "scms.order.released.v1",
            "event_id": str(event_id or uuid.uuid4()),
            "occurred_at": timezone.now().isoformat(),
            "terminal": {"id": 1, "code": "TEST"},
            "data": {
                "order": {
                    "id": 41,
                    "status": "released",
                    "priority": 20,
                    "container_number": 101,
                    "from_position": 11,
                    "to_position": 12,
                    "released_at": timezone.now().isoformat(),
                },
                "legs": [{
                    "id": 4101,
                    "sequence": 1,
                    "leg_kind": "move",
                    "status": "planned",
                    "equipment_type": 4,
                    "movement_mode": "road",
                    "start_position": 11,
                    "end_position": 12,
                    "source_inventory_position_id": 111,
                    "destination_inventory_position_id": 112,
                }],
            },
        }

    def equipment_payload(self):
        return {
            "schema": "scms.equipment.state.v1",
            "event_id": str(uuid.uuid4()),
            "terminal": {"id": 1, "code": "TEST"},
            "data": {
                "che_id": 17,
                "name": "SC01",
                "equipment_type_id": 4,
                "equipment_type_code": "SC",
                "powered_on": True,
                "working": False,
                "is_available": True,
                "is_dispatched": False,
                "is_in_maintenance": False,
                "latitude": 17.94,
                "longitude": -102.15,
                "rotation": 90.0,
                "fuel_level": 80.0,
                "timestamp": timezone.now().isoformat(),
            },
        }

    def setUp(self):
        self.order = ingest_released_order(
            "scms/v1/TEST/tos/orders/41/released",
            self.released_order_payload(),
        )
        self.equipment = ingest_equipment_state(
            "scms/v1/TEST/simulator/che/17/state",
            self.equipment_payload(),
        )

    def test_released_order_ingestion_is_idempotent(self):
        event_id = uuid.uuid4()
        payload = self.released_order_payload(event_id)
        payload["data"]["order"]["id"] = 42
        payload["data"]["legs"][0]["id"] = 4201
        first = ingest_released_order("topic", payload)
        second = ingest_released_order("topic", payload)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ExecutionOrder.objects.filter(tos_order_id=42).count(), 1)
        self.assertEqual(InboxEvent.objects.filter(event_id=event_id).count(), 1)

    def test_dispatch_creates_assignment_command_and_outbox(self):
        assignment = dispatch_next_operation(self.order.pk)

        self.assertEqual(assignment.equipment, self.equipment)
        self.assertEqual(assignment.operation.status, ExecutionOperation.DISPATCHED)
        self.order.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.IN_PROGRESS)
        self.assertTrue(self.equipment.is_dispatched)
        self.assertFalse(self.equipment.is_available)
        command = assignment.commands.get()
        self.assertEqual(command.payload["data"]["destination_position_id"], 12)
        self.assertEqual(command.payload["data"]["destination_inventory_position_id"], 112)
        self.assertEqual(OutboxEvent.objects.filter(event_id=command.command_id).count(), 1)

    def test_command_events_complete_operation_and_release_equipment(self):
        assignment = dispatch_next_operation(self.order.pk)
        command = assignment.commands.get()

        apply_command_event(command.command_id, "started")
        assignment.operation.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(assignment.operation.status, ExecutionOperation.WORKING)
        self.assertTrue(self.equipment.working)

        apply_command_event(command.command_id, "completed")
        assignment.operation.refresh_from_db()
        self.order.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(assignment.operation.status, ExecutionOperation.COMPLETE)
        self.assertEqual(self.order.status, ExecutionOrder.COMPLETE)
        self.assertFalse(self.equipment.working)
        self.assertFalse(self.equipment.is_dispatched)
        self.assertTrue(self.equipment.is_available)

    def test_stale_equipment_is_not_dispatched(self):
        self.equipment.last_seen_at = timezone.now() - timedelta(minutes=2)
        self.equipment.save(update_fields=["last_seen_at"])
        with self.assertRaisesMessage(ValidationError, "No fresh, available CHE"):
            dispatch_next_operation(self.order.pk)
        self.assertFalse(DispatchAssignment.objects.exists())


@override_settings(OES_API_TOKEN="secret", DEBUG=False)
class APIAuthenticationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_internal_api_requires_service_token(self):
        response = self.client.get(reverse("execution-order-list"))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(
            reverse("execution-order-list"),
            HTTP_X_OES_TOKEN="secret",
        )
        self.assertEqual(response.status_code, 200)

    def test_health_is_public(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "terminal-oes")
