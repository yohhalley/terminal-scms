"""TCL-005 producer-side contract tests for ``scms.execution.event.v1``.

The OES producer (``queue_execution_event`` via ``dispatch_next_operation`` and
``apply_command_event``) is driven through one full leg. The payloads are then
captured exactly as the outbox publisher puts them on the wire
(``publish_outbox._publish_batch``), and compared with the golden fixtures under
``<repo>/contracts/golden/scms.execution.event.v1/``. MiniTOS replays those same
fixtures through its real consumer (``minitos_core/test_contract_execution_events.py``).

Regenerate the fixtures after an intended producer change with:
    SCMS_UPDATE_GOLDEN=1 python manage.py test execution.test_contract_execution_events

Set ``SCMS_CONTRACT_STRICT=1`` to turn the known-bug ``expectedFailure`` markers
off and see the real assertion messages.
"""

import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .management.commands.publish_outbox import Command as PublishOutboxCommand
from .models import (
    EnterpriseProjection,
    EquipmentCommand,
    EquipmentState,
    OutboxEvent,
    SimulationSession,
    SiteProjection,
)
from .services.dispatch import apply_command_event, dispatch_next_operation
from .services.ingestion import ingest_released_order
from .sparkplug import decode_payload, metrics_by_name

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = REPO_ROOT / "contracts"
GOLDEN_DIR = CONTRACTS_DIR / "golden" / "scms.execution.event.v1"
SCHEMA_PATH = CONTRACTS_DIR / "schemas" / "scms.execution.event.v1.schema.json"
EXECUTION_SCHEMA = "scms.execution.event.v1"

ENTERPRISE_ID = uuid.UUID("0a0a0a0a-0000-4000-8000-000000000001")
SITE_ID = uuid.UUID("0b0b0b0b-0000-4000-8000-000000000002")
VOLATILE = "<volatile>"

# Order in which one leg's business events are produced, and their golden file names.
GOLDEN_SEQUENCE = [
    "01_leg_dispatched",
    "02_leg_started",
    "03_container_picked_up",
    "04_container_dropped",
    "05_leg_completed",
]


def known_bug(reason):
    """Mark a test that documents an open production bug.

    It stays a real assertion: when the bug is fixed the test passes and the
    suite reports an *unexpected success*, which forces the marker to be removed.
    """
    def decorator(func):
        func.__doc__ = f"KNOWN BUG: {reason}\n\n{func.__doc__ or ''}"
        if os.environ.get("SCMS_CONTRACT_STRICT"):
            return func
        return unittest.expectedFailure(func)
    return decorator


def normalise(document):
    """Blank fields that differ on every run (random ids and wall-clock times)."""
    document = json.loads(json.dumps(document))
    document["payload"]["event_id"] = VOLATILE
    document["payload"]["data"]["occurred_at"] = VOLATILE
    return document


def check_schema(instance, schema, path="$"):
    """Minimal stdlib JSON-Schema subset: type, required, properties, const, enum."""
    errors = []
    expected = schema.get("type")
    types = {
        "object": dict, "string": str, "integer": int, "array": list,
        "boolean": bool, "null": type(None),
    }
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(
            isinstance(instance, types[name]) and not (
                name == "integer" and isinstance(instance, bool)
            )
            for name in allowed
        ):
            return [f"{path}: expected {expected}, got {type(instance).__name__}"]
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        for key, child in schema.get("properties", {}).items():
            if key in instance:
                errors.extend(check_schema(instance[key], child, f"{path}.{key}"))
    return errors


@override_settings(OES_API_TOKEN="contract-test-token")
class ExecutionEventContractTests(TestCase):
    def setUp(self):
        self.enterprise = EnterpriseProjection.objects.create(
            source_id=ENTERPRISE_ID, code="enterprise-a", name="Enterprise A",
        )
        self.site = SiteProjection.objects.create(
            source_id=SITE_ID, enterprise=self.enterprise,
            source_location_id=1, code="site-a", name="Site A",
            lifecycle_status="published", projection_status=SiteProjection.READY,
            source_revision=1, snapshot_checksum="a" * 64,
        )
        self.order = ingest_released_order(
            "scms/v2/enterprise-a/site-a/tos/orders/41/released",
            self.released_order_payload(41, 4101),
        )
        self.session = SimulationSession.objects.create(
            site=self.site,
            configuration_revision=1,
            configuration_checksum="a" * 64,
            sparkplug_group_id="ENTERPRISE_A",
            sparkplug_node_id="SCMS_ENTERPRISE_A_SITE_A",
            actual_state=SimulationSession.RUNNING,
            node_connected=True,
        )
        self.equipment = EquipmentState.objects.create(
            site=self.site, tos_che_id=17, name="SC01",
            equipment_type_id=4, equipment_type_code="SC",
            powered_on=True, is_available=True,
            latitude=17.94, longitude=-102.15,
            source_timestamp=timezone.now(), last_seen_at=timezone.now(),
            simulation_session=self.session, sparkplug_device_id="CHE_17",
            device_connected=True, data_valid=True,
        )

    def released_order_payload(self, order_id, leg_id):
        return {
            "schema": "scms.order.released.v1",
            "event_id": str(uuid.uuid4()),
            "occurred_at": timezone.now().isoformat(),
            "enterprise": {"id": str(ENTERPRISE_ID), "code": "enterprise-a", "name": "Enterprise A"},
            "site": {
                "id": str(SITE_ID), "code": "site-a", "name": "Site A",
                "source_location_id": 1,
            },
            "terminal": {"id": 1, "code": "TEST"},
            "data": {
                "order": {
                    "id": order_id, "status": "released", "priority": 20,
                    "container_number": 101, "from_position": 11, "to_position": 12,
                    "released_at": timezone.now().isoformat(),
                },
                "legs": [{
                    "id": leg_id, "sequence": 1, "leg_kind": "move",
                    "status": "planned", "equipment_type": 4, "movement_mode": "road",
                    "start_position": 11, "end_position": 12,
                    "source_inventory_position_id": 111,
                    "destination_inventory_position_id": 112,
                    "source_latitude": 17.9401, "source_longitude": -102.1501,
                    "destination_latitude": 17.9408, "destination_longitude": -102.1494,
                }],
            },
        }

    def run_one_leg(self):
        """Drive the real producer the way the Sparkplug result path does."""
        assignment = dispatch_next_operation(self.order.pk)
        command = assignment.commands.get()
        apply_command_event(command.command_id, "acknowledged")
        apply_command_event(command.command_id, "started")
        # Godot reports the pickup without a PositionId (HeadlessSimulator.gd:247).
        apply_command_event(command.command_id, "container_picked_up")
        # Godot reports the drop with the destination inventory position (HS:274-275).
        apply_command_event(command.command_id, "container_dropped", position_id=112)
        apply_command_event(command.command_id, "completed")
        return command

    def publish_and_capture(self):
        """Run the real outbox publisher against a fake broker; return wire messages."""
        client = Mock()
        published = []

        def publish(topic, payload, qos=0, retain=False):
            published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
            result = Mock()
            result.is_published.return_value = True
            return result

        client.publish.side_effect = publish
        PublishOutboxCommand()._publish_batch(client)
        return published

    def produced_execution_events(self):
        self.run_one_leg()
        documents = []
        for message in self.publish_and_capture():
            if isinstance(message["payload"], (bytes, bytearray)):
                continue  # Sparkplug DCMD, not a business event
            payload = json.loads(message["payload"])
            if payload.get("schema") != EXECUTION_SCHEMA:
                continue
            documents.append({
                "topic": message["topic"],
                "qos": message["qos"],
                "retain": message["retain"],
                "payload": payload,
            })
        return documents

    def test_producer_matches_golden_fixtures(self):
        """The committed golden files are exactly what OES puts on the wire today."""
        documents = self.produced_execution_events()
        self.assertEqual(
            [doc["payload"]["data"]["event"] for doc in documents],
            [name.split("_", 1)[1] for name in GOLDEN_SEQUENCE],
        )
        if not CONTRACTS_DIR.is_dir():
            self.skipTest(f"{CONTRACTS_DIR} is not available (app-only Docker image)")
        if os.environ.get("SCMS_UPDATE_GOLDEN"):
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            for name, document in zip(GOLDEN_SEQUENCE, documents):
                (GOLDEN_DIR / f"{name}.json").write_text(
                    json.dumps(document, indent=2, sort_keys=True) + "\n",
                )
        for name, document in zip(GOLDEN_SEQUENCE, documents):
            path = GOLDEN_DIR / f"{name}.json"
            self.assertTrue(path.exists(), f"Missing {path}; run with SCMS_UPDATE_GOLDEN=1")
            golden = json.loads(path.read_text())
            self.assertEqual(
                normalise(document), normalise(golden),
                f"{path.name} drifted from the OES producer",
            )

    @known_bug(
        "B3/A1: OES execution events have no 'terminal' key, but MiniTOS "
        "apply_execution_message requires payload['terminal']['id'] "
        "(events.py:19-40 vs integration_events.py:27-36)."
    )
    def test_every_execution_event_carries_terminal_id(self):
        """Decided envelope (BE review 2.7, T-006): terminal.id is present on every event."""
        documents = self.produced_execution_events()
        self.assertTrue(documents)
        for document in documents:
            payload = document["payload"]
            event = payload["data"]["event"]
            self.assertIn("terminal", payload, f"{event}: envelope has no 'terminal' key")
            self.assertIsInstance(payload["terminal"].get("id"), int, f"{event}: terminal.id")

    @known_bug(
        "B3/A1: produced envelope fails the agreed scms.execution.event.v1 "
        "schema because 'terminal' is missing."
    )
    def test_produced_events_validate_against_contract_schema(self):
        if not SCHEMA_PATH.exists():
            self.skipTest(f"{SCHEMA_PATH} is not available (app-only Docker image)")
        schema = json.loads(SCHEMA_PATH.read_text())
        errors = []
        for document in self.produced_execution_events():
            errors.extend(
                f"{document['payload']['data']['event']}: {error}"
                for error in check_schema(document["payload"], schema)
            )
        self.assertEqual(errors, [])


@override_settings(OES_API_TOKEN="contract-test-token")
class CancelledCommandRegressionTests(ExecutionEventContractTests):
    """TCL-005 / gap B10: cancelling in OES leaves Godot holding the command."""

    # Don't re-run the inherited contract tests in this class.
    test_producer_matches_golden_fixtures = None
    test_every_execution_event_carries_terminal_id = None
    test_produced_events_validate_against_contract_schema = None

    def cancel_via_api(self, command):
        response = APIClient().post(
            reverse("equipment-command-event", args=[command.command_id]),
            {"event": "cancelled", "reason": "TCL-005 regression"},
            format="json",
            HTTP_X_OES_TOKEN="contract-test-token",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def dispatch_and_start(self):
        assignment = dispatch_next_operation(self.order.pk)
        command = assignment.commands.get()
        apply_command_event(command.command_id, "acknowledged")
        apply_command_event(command.command_id, "started")
        return command

    def test_cancel_frees_che_in_oes_and_next_order_is_dispatched_to_it(self):
        """Pins today's OES behaviour (the precondition of B10); passes now."""
        command = self.dispatch_and_start()
        self.cancel_via_api(command)
        command.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(command.status, EquipmentCommand.CANCELLED)
        self.assertTrue(self.equipment.is_available)
        self.assertFalse(self.equipment.is_dispatched)
        self.assertFalse(self.equipment.working)

        second_order = ingest_released_order(
            "scms/v2/enterprise-a/site-a/tos/orders/42/released",
            self.released_order_payload(42, 4201),
        )
        second = dispatch_next_operation(second_order.pk).commands.get()
        self.assertEqual(second.topic, command.topic)
        self.assertNotEqual(second.command_id, command.command_id)
        # Godot still holds `command` (it was never told), so it will reject `second`
        # with "CHE already has an active command" (HeadlessSimulator.gd:201-202).

    @known_bug(
        "B4/B10: POST /api/commands/{id}/event/ 'cancelled' queues no DCMD to the "
        "CHE (dispatch.py:276-321), so Godot keeps the command and rejects the next one."
    )
    def test_cancel_queues_abort_dcmd_to_the_che(self):
        """After an OES cancel, an outbox DCMD for that CHE must reference the cancelled
        command. The Command/Type value is not agreed yet (TBD with Able Archer), so it
        is not asserted."""
        command = self.dispatch_and_start()
        before = set(OutboxEvent.objects.values_list("event_id", flat=True))
        self.cancel_via_api(command)
        new_dcmds = [
            event for event in OutboxEvent.objects.exclude(event_id__in=before)
            if event.topic == command.topic and event.payload_bytes is not None
        ]
        self.assertEqual(len(new_dcmds), 1, "cancel queued no DCMD for the CHE")
        metrics = metrics_by_name(decode_payload(bytes(new_dcmds[0].payload_bytes)))
        self.assertIn(str(command.command_id), [str(value) for value in metrics.values()])
