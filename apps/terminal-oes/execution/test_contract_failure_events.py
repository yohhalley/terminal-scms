"""TCL-005 contract tests for ``leg_failed`` (``scms.execution.event.v1``) and its
optional ``data.detail``.

TCL-028 (§3.2c) makes OES copy Godot's ``Command/Result/Detail`` into
``data.detail`` of ``leg_failed`` and leave the key out when the detail is
empty. ``data.reason`` stays Godot's raw ``Command/Result/Reason`` code.

The leg is driven through the real Sparkplug result path
(``sparkplug_runtime._apply_command_result``), the way a Godot DDATA result
arrives, and captured from the real outbox publisher. Goldens:
``<repo>/contracts/golden/scms.execution.event.v1/failure/``. MiniTOS replays them
in ``minitos_core/test_contract_failure_events.py``.

Until TCL-028 is merged the detail-emitting check is skipped (feature absent:
``apply_command_event`` has no ``detail`` parameter); with
``SCMS_CONTRACT_STRICT=1`` it fails instead, so strict runs flag the gap. Once
TCL-028 lands it runs as a normal test; no marker has to be removed.

Regenerate the goldens (on a branch that has TCL-028) with:
    SCMS_UPDATE_GOLDEN=1 python manage.py test execution.test_contract_failure_events
"""

import inspect
import json
import os
import uuid

from django.test import override_settings

from .services.dispatch import apply_command_event, dispatch_next_operation
from .services.sparkplug_runtime import _apply_command_result
from . import test_contract_execution_events as one_leg
from .test_contract_execution_events import (
    CONTRACTS_DIR,
    GOLDEN_DIR,
    SCHEMA_PATH,
    check_schema,
    normalise,
)

FAILURE_GOLDEN_DIR = GOLDEN_DIR / "failure"
WITH_DETAIL = "01_leg_failed_with_detail"
WITHOUT_DETAIL = "02_leg_failed_without_detail"
GODOT_REASON = "che_busy"
GODOT_DETAIL = "CHE already has an active command"
RESULT_NS = uuid.UUID("5c0a1b2e-7d6f-4e3a-9b8c-0d1e2f3a4b5c")

# TCL-028 adds ``detail`` to apply_command_event (and the Sparkplug result path).
TCL028_FAILURE_DETAIL = "detail" in inspect.signature(apply_command_event).parameters


@override_settings(OES_API_TOKEN="contract-test-token")
class LegFailedContractTests(one_leg.ExecutionEventContractTests):
    # Base class imported via its module so the loader does not run it twice here.
    # Don't re-run the inherited one-leg contract tests in this class.
    test_producer_matches_golden_fixtures = None
    test_every_execution_event_carries_terminal_id = None
    test_produced_events_validate_against_contract_schema = None

    def require_tcl028(self):
        if TCL028_FAILURE_DETAIL:
            return
        message = (
            "TCL-028 not merged: OES does not copy Command/Result/Detail into "
            "leg_failed data.detail yet"
        )
        if os.environ.get("SCMS_CONTRACT_STRICT"):
            self.fail(message)
        self.skipTest(message)

    def godot_result(self, command, event, seq, **extra):
        metrics = {
            "Command/Result/Id": str(uuid.uuid5(RESULT_NS, f"{command.command_id}:{event}:{seq}")),
            "Command/Result/CommandId": str(command.command_id),
            "Command/Result/Event": event,
            "Session/Id": str(self.session.pk),
            "Sim/EventSeq": seq,
        }
        metrics.update(extra)
        return _apply_command_result(metrics, self.session)

    def failed_leg_event(self, **failure_metrics):
        """dispatch -> acknowledged -> started -> failed (Godot result); the leg_failed doc."""
        command = dispatch_next_operation(self.order.pk).commands.get()
        self.godot_result(command, "acknowledged", 1)
        self.godot_result(command, "started", 2)
        receipt = self.godot_result(command, "failed", 3, **failure_metrics)
        self.assertEqual(receipt.status, "applied", receipt.reason)
        documents = [
            doc for doc in self.publish_and_capture_execution()
            if doc["payload"]["data"]["event"] == "leg_failed"
        ]
        self.assertEqual(len(documents), 1, "expected exactly one leg_failed")
        return documents[0]

    def publish_and_capture_execution(self):
        documents = []
        for message in self.publish_and_capture():
            if isinstance(message["payload"], (bytes, bytearray)):
                continue  # Sparkplug DCMD
            payload = json.loads(message["payload"])
            if payload.get("schema") == "scms.execution.event.v1":
                documents.append({
                    "topic": message["topic"], "qos": message["qos"],
                    "retain": message["retain"], "payload": payload,
                })
        return documents

    def assert_matches_golden(self, name, document):
        if not CONTRACTS_DIR.is_dir():
            self.skipTest(f"{CONTRACTS_DIR} is not available (app-only Docker image)")
        path = FAILURE_GOLDEN_DIR / f"{name}.json"
        if os.environ.get("SCMS_UPDATE_GOLDEN"):
            FAILURE_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        self.assertTrue(path.exists(), f"Missing {path}; run with SCMS_UPDATE_GOLDEN=1")
        self.assertEqual(
            normalise(document), normalise(json.loads(path.read_text())),
            f"{path.name} drifted from the OES producer",
        )

    def assert_schema_valid(self, payload):
        if not SCHEMA_PATH.exists():
            self.skipTest(f"{SCHEMA_PATH} is not available (app-only Docker image)")
        self.assertEqual(check_schema(payload, json.loads(SCHEMA_PATH.read_text())), [])

    def test_schema_accepts_leg_failed_with_and_without_detail(self):
        """data.detail is an optional string on leg_failed (TCL-028 §3.2c)."""
        if not SCHEMA_PATH.exists() or not FAILURE_GOLDEN_DIR.is_dir():
            self.skipTest("contracts/ is not available (app-only Docker image)")
        schema = json.loads(SCHEMA_PATH.read_text())
        with_detail = json.loads((FAILURE_GOLDEN_DIR / f"{WITH_DETAIL}.json").read_text())
        without_detail = json.loads((FAILURE_GOLDEN_DIR / f"{WITHOUT_DETAIL}.json").read_text())
        self.assertEqual(with_detail["payload"]["data"]["event"], "leg_failed")
        self.assertIn("detail", with_detail["payload"]["data"])
        self.assertNotIn("detail", without_detail["payload"]["data"])
        self.assertEqual(check_schema(with_detail["payload"], schema), [])
        self.assertEqual(check_schema(without_detail["payload"], schema), [])
        stripped = json.loads(json.dumps(with_detail["payload"]))
        del stripped["data"]["detail"]
        self.assertEqual(check_schema(stripped, schema), [])
        wrong_type = json.loads(json.dumps(with_detail["payload"]))
        wrong_type["data"]["detail"] = 42
        self.assertTrue(check_schema(wrong_type, schema), "a non-string detail must be rejected")

    def test_leg_failed_carries_godot_detail(self):
        """TCL-028: Command/Result/Detail -> data.detail; reason is the raw Godot code."""
        self.require_tcl028()
        document = self.failed_leg_event(**{
            "Command/Result/Reason": GODOT_REASON,
            "Command/Result/Detail": GODOT_DETAIL,
        })
        data = document["payload"]["data"]
        self.assertEqual(data["reason"], GODOT_REASON)
        self.assertEqual(data["detail"], GODOT_DETAIL)
        self.assert_schema_valid(document["payload"])
        self.assert_matches_golden(WITH_DETAIL, document)

    def test_leg_failed_omits_empty_or_missing_detail(self):
        """An empty Command/Result/Detail, or none at all, leaves data.detail out."""
        document = self.failed_leg_event(**{
            "Command/Result/Reason": GODOT_REASON,
            "Command/Result/Detail": "",
        })
        data = document["payload"]["data"]
        self.assertEqual(data["reason"], GODOT_REASON)
        self.assertNotIn("detail", data)
        self.assert_schema_valid(document["payload"])
        self.assert_matches_golden(WITHOUT_DETAIL, document)

    def test_leg_failed_without_detail_metric_omits_detail(self):
        document = self.failed_leg_event(**{"Command/Result/Reason": GODOT_REASON})
        self.assertNotIn("detail", document["payload"]["data"])
