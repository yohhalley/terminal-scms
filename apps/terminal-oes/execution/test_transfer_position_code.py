"""BL-53c: paired hand-off DCMDs carry Command/TransferPositionCode.

Godot's RTG block check (HeadlessSimulator.gd ``_rtg_leg_rejection``) takes the
block from the ``-TL`` lane code in Command/TransferPositionCode. OES sends it
next to Command/TransferPositionId on both sides of every hand-off: H1 (QC
giver ``qc_discharge``, TT receiver ``execute_order_leg``) and H2 (TT giver
``assign_handoff``, RTG receiver ``execute_order_leg``).
"""

import copy
from types import SimpleNamespace

from django.test import TestCase

from .services.dispatch import dispatch_next_operation, snapshot_lane_code
from .services.simulations import (
    bootstrap_document,
    che_block_codes,
    claim_next_session,
    start_simulation,
)
from .test_handoffs import HandoffTestBase
from .test_scenario_reset import project_import_v1

QC_LANE_ID, BLOCK_LANE_ID = 6, 7
# Shaped like the MiniTOS execution snapshot (import_v1): lane positions carry
# their lane in logical_address['interface'].
SNAPSHOT_LAYOUT = {
    "layout": {
        "transfer_interfaces": [{"id": 1, "code": "QC01-L1"}, {"id": 2, "code": "BLOCK01-TL"}],
        "interface_positions": [
            {"id": 31, "code": "QC01-L1.01", "transfer_interface_id": 1},
            {"id": 32, "code": "BLOCK01-TL.01", "transfer_interface_id": 2},
        ],
        "operational_positions": [
            {"id": 5, "code": "V001.0330682", "interface_position_id": None, "logical_address": {}},
            {"id": QC_LANE_ID, "code": "QC01-L1.01", "interface_position_id": 31,
             "logical_address": {"interface": "QC01-L1"}},
            {"id": BLOCK_LANE_ID, "code": "BLOCK01-TL.01", "interface_position_id": 32,
             "logical_address": {"interface": "BLOCK01-TL"}},
            {"id": 12, "code": "BLOCK01.1.1.01", "interface_position_id": None, "logical_address": {}},
        ],
    },
}


class TransferPositionCodeTests(HandoffTestBase):
    def setUp(self):
        super().setUp()
        self.site.snapshot_payload = copy.deepcopy(SNAPSHOT_LAYOUT)
        self.site.save(update_fields=["snapshot_payload"])

    def h1_commands(self):
        dispatch_next_operation(self.order.pk)
        tt = self.chosen_tt()
        return self.command_for(self.qc), tt, self.command_for(tt)

    def assert_transfer(self, command, position_id, code):
        metrics = self.dcmd_metrics(command)
        self.assertEqual(metrics["Command/TransferPositionId"], position_id)
        self.assertEqual(metrics.get("Command/TransferPositionCode"), code)
        self.assertEqual(command.payload["data"]["transfer_position_code"], code)

    def test_h1_giver_and_receiver_carry_the_qc_lane_code(self):
        qc_cmd, _tt, tt_cmd = self.h1_commands()
        self.assertEqual(self.dcmd_metrics(qc_cmd)["Command/HandoffRole"], "giver")
        self.assertEqual(self.dcmd_metrics(tt_cmd)["Command/HandoffRole"], "receiver")
        self.assert_transfer(qc_cmd, QC_LANE_ID, "QC01-L1")
        self.assert_transfer(tt_cmd, QC_LANE_ID, "QC01-L1")

    def test_h2_giver_and_receiver_carry_the_block_lane_code(self):
        _tt, _tt_cmd, h2_cmd, rtg_cmd = self.run_h2()
        self.assertEqual(h2_cmd.command_type, "assign_handoff")
        self.assertEqual(self.dcmd_metrics(h2_cmd)["Command/HandoffRole"], "giver")
        self.assertEqual(self.dcmd_metrics(rtg_cmd)["Command/HandoffRole"], "receiver")
        self.assert_transfer(h2_cmd, BLOCK_LANE_ID, "BLOCK01-TL")
        self.assert_transfer(rtg_cmd, BLOCK_LANE_ID, "BLOCK01-TL")
        # Godot's lane_block_code() needs a code ending in "-TL" (block = prefix).
        self.assertTrue(self.dcmd_metrics(rtg_cmd)["Command/TransferPositionCode"].endswith("-TL"))

    def test_lane_found_through_interface_positions_without_logical_address(self):
        for row in self.site.snapshot_payload["layout"]["operational_positions"]:
            row["logical_address"] = {}
        self.site.save(update_fields=["snapshot_payload"])

        qc_cmd, _tt, tt_cmd = self.h1_commands()

        self.assert_transfer(qc_cmd, QC_LANE_ID, "QC01-L1")
        self.assert_transfer(tt_cmd, QC_LANE_ID, "QC01-L1")

    def test_unknown_code_omits_the_metric_but_keeps_the_id(self):
        self.site.snapshot_payload = {}
        self.site.save(update_fields=["snapshot_payload"])

        qc_cmd, _tt, tt_cmd = self.h1_commands()

        for command in (qc_cmd, tt_cmd):
            metrics = self.dcmd_metrics(command)
            self.assertEqual(metrics["Command/TransferPositionId"], QC_LANE_ID)
            self.assertNotIn("Command/TransferPositionCode", metrics)
            self.assertIsNone(command.payload["data"]["transfer_position_code"])

    def test_no_transfer_position_sends_neither_metric(self):
        for operation in self.order.operations.filter(sequence__in=(1, 2)):
            if operation.sequence == 1:
                operation.destination_position_id = None
            else:
                operation.source_position_id = None
            operation.save(update_fields=["destination_position_id", "source_position_id"])

        qc_cmd, _tt, tt_cmd = self.h1_commands()

        for command in (qc_cmd, tt_cmd):
            metrics = self.dcmd_metrics(command)
            self.assertIn("Command/HandoffId", metrics)
            self.assertNotIn("Command/TransferPositionId", metrics)
            self.assertNotIn("Command/TransferPositionCode", metrics)


class SnapshotLaneCodeTests(HandoffTestBase):
    def test_lookup(self):
        site = SimpleNamespace(snapshot_payload=SNAPSHOT_LAYOUT)
        self.assertEqual(snapshot_lane_code(site, BLOCK_LANE_ID), "BLOCK01-TL")
        self.assertIsNone(snapshot_lane_code(site, 12))  # a yard slot is not on a lane
        self.assertIsNone(snapshot_lane_code(site, 999))
        self.assertIsNone(snapshot_lane_code(site, None))
        self.assertIsNone(snapshot_lane_code(SimpleNamespace(snapshot_payload={}), BLOCK_LANE_ID))
        self.assertIsNone(snapshot_lane_code(SimpleNamespace(snapshot_payload=None), BLOCK_LANE_ID))


def godot_lane_block_code(lane_code):
    """Python twin of HeadlessSimulator.gd ``lane_block_code`` (BLOCK01-TL ->
    BLOCK01; "" unless the code ends in -TL)."""
    code = (lane_code or "").strip()
    return code[:-3] if code.endswith("-TL") else ""


class ImportV1RtgBlockCheckInputsTests(TestCase):
    """On the real import_v1 snapshot (OES fixture), both inputs of Godot's RTG
    block check are present and agree: the RTG01 bootstrap CHE has
    ``block_code`` BLOCK01 and the H2 lane code is BLOCK01-TL."""

    def setUp(self):
        self.site, _events = project_import_v1()
        layout = self.site.snapshot_payload["layout"]
        self.positions = {row["code"]: row["id"] for row in layout["operational_positions"]}
        self.ches = {row["name"]: row["id"] for row in self.site.snapshot_payload["equipment"]["ches"]}

    def test_lane_codes_of_the_hand_off_positions(self):
        self.assertEqual(snapshot_lane_code(self.site, self.positions["QC01-L1.01"]), "QC01-L1")
        self.assertEqual(snapshot_lane_code(self.site, self.positions["BLOCK01-TL.01"]), "BLOCK01-TL")

    def test_rtg_block_code_from_current_asset(self):
        self.assertEqual(che_block_codes(self.site.snapshot_payload), {self.ches["RTG01"]: "BLOCK01"})

    def test_bootstrap_carries_block_code_for_the_rtg_only(self):
        start_simulation(self.site.pk)
        session, token = claim_next_session("runner-test")
        rows = {row["name"]: row for row in bootstrap_document(session, token)["equipment_state"]}
        self.assertEqual(rows["RTG01"]["block_code"], "BLOCK01")
        for name in ("QC01", "TT01", "TT02"):
            self.assertNotIn("block_code", rows[name])

    def test_godot_check_would_compare_matching_blocks(self):
        lane = snapshot_lane_code(self.site, self.positions["BLOCK01-TL.01"])
        served = che_block_codes(self.site.snapshot_payload)[self.ches["RTG01"]]
        self.assertEqual(godot_lane_block_code(lane), "BLOCK01")
        self.assertEqual(godot_lane_block_code(lane), served)
        # The position code alone would not switch the check on.
        self.assertEqual(godot_lane_block_code("BLOCK01-TL.01"), "")
