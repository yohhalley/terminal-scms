"""TCL-005: contract tests for the TCL-003a hand-off execution events.

container_transferred (T-006) and handoff_exception (T-007/D-013, reason per D-017) only exist once
TCL-003a (paired hand-off dispatch) is merged. Before that this module is skipped.

Drives the real producer through TCL-003a's own scenario helpers
(``test_handoffs.HandoffTestBase``) and the real outbox publisher, then compares the
wire messages with ``contracts/golden/scms.execution.event.v1/handoff/*.json`` and
validates every produced execution event against the contract schema.
"""

import json
import os
import unittest

from . import test_contract_execution_events as contract
from .models import EquipmentCommand

try:
    from . import test_handoffs as handoffs
except ImportError as exc:  # main before TCL-003a: no hand-off producer yet
    raise unittest.SkipTest(f"TCL-003a hand-off producer not present: {exc}")

HANDOFF_GOLDEN_DIR = contract.GOLDEN_DIR / "handoff"


def normalise(document):
    document = contract.normalise(document)
    # handoff_id is derived from the session pk, which depends on test ordering.
    document["payload"]["data"]["handoff_id"] = contract.VOLATILE
    return document


class HandoffEventContractTests(handoffs.HandoffTestBase):
    publish_and_capture = contract.ExecutionEventContractTests.publish_and_capture

    def execution_messages(self):
        return [
            {**doc, "payload": json.loads(doc["payload"])}
            for doc in self.publish_and_capture()
            if doc["topic"].endswith("/execution")  # skip binary Sparkplug DCMDs
        ]

    def happy_path_messages(self):
        self.run_happy_path()
        return self.execution_messages()

    def no_show_messages(self):
        qc_cmd, _tt, _tt_cmd = self.run_h1(tt_ready_ms=10**9)
        self.result(qc_cmd, "waiting_for_handoff", 20_000)
        self.result(qc_cmd, "handoff_timed_out", 320_000)
        return self.execution_messages()

    def check_golden(self, name, document):
        if not contract.CONTRACTS_DIR.is_dir():
            self.skipTest(f"{contract.CONTRACTS_DIR} is not available (app-only Docker image)")
        path = HANDOFF_GOLDEN_DIR / f"{name}.json"
        if os.environ.get("SCMS_UPDATE_GOLDEN"):
            HANDOFF_GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
        self.assertTrue(path.exists(), f"Missing {path}; run with SCMS_UPDATE_GOLDEN=1")
        golden = json.loads(path.read_text())
        self.assertEqual(normalise(document), normalise(golden), f"{name} drifted")

    def assert_schema_valid(self, documents):
        schema = json.loads(contract.SCHEMA_PATH.read_text())
        for doc in documents:
            errors = contract.check_schema(doc["payload"], schema)
            self.assertEqual(errors, [], doc["payload"]["data"]["event"])

    def test_container_transferred_matches_golden_and_schema(self):
        documents = self.happy_path_messages()
        transfers = [d for d in documents if d["payload"]["data"]["event"] == "container_transferred"]
        self.assertEqual(len(transfers), 2, "one custody event per hand-off (H1 QC->TT, H2 TT->RTG)")
        self.check_golden("01_container_transferred_qc_to_tt", transfers[0])
        self.check_golden("02_container_transferred_tt_to_rtg", transfers[1])
        self.assert_schema_valid(documents)

    def test_handoff_exception_matches_golden_and_schema(self):
        documents = self.no_show_messages()
        exceptions = [d for d in documents if d["payload"]["data"]["event"] == "handoff_exception"]
        self.assertEqual(len(exceptions), 1)
        self.check_golden("03_handoff_exception_simulator_failure", exceptions[0])
        self.assert_schema_valid(documents)

    def test_schema_rejects_hand_off_events_without_their_required_fields(self):
        schema = json.loads(contract.SCHEMA_PATH.read_text())
        for name, field in [
            ("01_container_transferred_qc_to_tt", "from_che_id"),
            ("01_container_transferred_qc_to_tt", "to_che_id"),
            ("03_handoff_exception_simulator_failure", "reason"),
            ("02_container_transferred_tt_to_rtg", "to_che_name"),
            ("03_handoff_exception_simulator_failure", "detail"),
        ]:
            path = HANDOFF_GOLDEN_DIR / f"{name}.json"
            if not path.exists():
                self.skipTest(f"Missing {path}")
            payload = json.loads(path.read_text())["payload"]
            del payload["data"][field]
            self.assertIn(
                f"$.data: missing required property '{field}'",
                contract.check_schema(payload, schema),
            )

    def test_schema_rejects_a_handoff_exception_reason_outside_the_d017_list(self):
        """D-017: reason is a closed, extensible list; NO_TT_TIMEOUT is superseded."""
        schema = json.loads(contract.SCHEMA_PATH.read_text())
        path = HANDOFF_GOLDEN_DIR / "03_handoff_exception_simulator_failure.json"
        if not path.exists():
            self.skipTest(f"Missing {path}")
        payload = json.loads(path.read_text())["payload"]
        self.assertEqual(contract.check_schema(payload, schema), [])
        payload["data"]["reason"] = "NO_TT_TIMEOUT"
        self.assertIn("$.data.reason: 'NO_TT_TIMEOUT' not in enum", contract.check_schema(payload, schema))

    def test_no_show_exception_carries_simulator_failure_and_detail(self):
        """D-017: reason SIMULATOR_FAILURE, detail 'no TT within <N> s' (default 300)."""
        (exception,) = [
            d for d in self.no_show_messages()
            if d["payload"]["data"]["event"] == "handoff_exception"
        ]
        self.assertEqual(exception["payload"]["data"]["reason"], "SIMULATOR_FAILURE")
        self.assertEqual(exception["payload"]["data"]["detail"], "no TT within 300 s")

    def test_no_show_cancel_dcmds_carry_reason_simulator_failure(self):
        """D-017: Command/Reason on both no-show cancel DCMDs is the same reason code."""
        self.no_show_messages()
        cancels = EquipmentCommand.objects.filter(command_type="cancel")
        self.assertEqual(cancels.count(), 2, "one cancel DCMD per hand-off leg (QC and TT)")
        for cancel in cancels:
            self.assertEqual(self.dcmd_metrics(cancel)["Command/Reason"], "SIMULATOR_FAILURE")
