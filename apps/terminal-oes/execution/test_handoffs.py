"""TCL-003a/TCL-028: paired hand-off dispatch, hand-off record, TT no-show, D-014, D-016.

Results are fed in the shape Godot TCL-014a publishes them (HeadlessSimulator.gd
``_publish_command_result``): Command/Result/*, Session/Id, Sim/TimeMs,
Event/TimeMs = Sim/StartEpochMs + Sim/TimeMs and Sim/EventSeq.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    CommandResultReceipt,
    EnterpriseProjection,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    Handoff,
    OutboxEvent,
    SimulationSession,
    SiteProjection,
)
from .services.dispatch import dispatch_next_operation, select_equipment
from .services.ingestion import ingest_released_order
from .services.sparkplug_runtime import _apply_command_result, process_sparkplug_message
from .sparkplug import decode_payload, encode_payload, metrics_by_name

START_EPOCH_MS = 1_790_000_000_000
SEED = 4242
CONTAINER = "MSCU1234565"
QC_TYPE, TT_TYPE, RTG_TYPE = 1, 2, 3
RESULT_NS = uuid.UUID("2b3f8a40-0000-4000-8000-00000000c0de")


class _Rollback(Exception):
    pass


class HandoffTestBase(TestCase):
    tt_ids = (11, 12)

    def setUp(self):
        self.enterprise = EnterpriseProjection.objects.create(
            source_id=uuid.UUID("0a0a0a0a-0000-4000-8000-000000000001"),
            code="enterprise-a", name="Enterprise A",
        )
        self.site = SiteProjection.objects.create(
            source_id=uuid.UUID("0b0b0b0b-0000-4000-8000-000000000002"),
            enterprise=self.enterprise, source_location_id=1, code="site-a", name="Site A",
            lifecycle_status="published", projection_status=SiteProjection.READY,
            source_revision=1, snapshot_checksum="a" * 64,
        )
        self.session = SimulationSession.objects.create(
            site=self.site, configuration_revision=1, configuration_checksum="a" * 64,
            sparkplug_group_id="ENTERPRISE_A", sparkplug_node_id="SCMS_ENTERPRISE_A_SITE_A",
            actual_state=SimulationSession.RUNNING, node_connected=True,
            seed=SEED, start_epoch_ms=START_EPOCH_MS,
        )
        self.qc = self.che(1, "QC01", QC_TYPE, "STS")
        self.tts = [self.che(che_id, f"TT{che_id - 10:02d}", TT_TYPE, "TT") for che_id in self.tt_ids]
        self.rtg = self.che(21, "RTG01", RTG_TYPE, "RTG")
        self.order = self.release(41)
        self.seq = 0

    def che(self, che_id, name, type_id, type_code, idle_since=None):
        return EquipmentState.objects.create(
            site=self.site, tos_che_id=che_id, name=name,
            equipment_type_id=type_id, equipment_type_code=type_code,
            powered_on=True, is_available=True, latitude=17.94, longitude=-102.15,
            source_timestamp=timezone.now(), last_seen_at=timezone.now(),
            simulation_session=self.session, sparkplug_device_id=name,
            device_connected=True, data_valid=True, idle_since_event_ms=idle_since,
        )

    def release(self, order_id):
        legs = [
            {"id": order_id * 100 + 1, "sequence": 1, "equipment_type": QC_TYPE,
             "start_position": 5, "end_position": 6},  # vessel slot → QC01-L1 (hand-off)
            {"id": order_id * 100 + 2, "sequence": 2, "equipment_type": TT_TYPE,
             "start_position": 6, "end_position": 7,  # QC01-L1 → BLOCK01-TL (hand-off)
             "source_latitude": 17.94, "source_longitude": -102.15,
             "destination_latitude": 17.941, "destination_longitude": -102.149},
            {"id": order_id * 100 + 3, "sequence": 3, "equipment_type": RTG_TYPE,
             "start_position": 7, "end_position": 12,
             "destination_inventory_position_id": 112,
             "destination_latitude": 17.9408, "destination_longitude": -102.1494},
        ]
        for leg in legs:
            leg.update({"leg_kind": "move", "status": "planned", "movement_mode": "road"})
        return ingest_released_order(
            f"scms/v2/enterprise-a/site-a/tos/orders/{order_id}/released",
            {
                "schema": "scms.order.released.v1",
                "event_id": str(uuid.uuid5(RESULT_NS, f"release:{order_id}")),
                "occurred_at": timezone.now().isoformat(),
                "enterprise": {"id": str(self.enterprise.source_id), "code": "enterprise-a",
                               "name": "Enterprise A"},
                "site": {"id": str(self.site.source_id), "code": "site-a", "name": "Site A",
                         "source_location_id": 1},
                "terminal": {"id": 1, "code": "site-a"},
                "data": {
                    "order": {"id": order_id, "status": "released", "priority": 20,
                              "container_number": 101, "container_unit_number": CONTAINER,
                              "released_at": timezone.now().isoformat()},
                    "legs": legs,
                },
            },
        )

    # --- Godot-shaped results -------------------------------------------
    def result(self, command, event, sim_ms, **extra):
        self.seq += 1
        metrics = {
            "Command/Result/Id": str(uuid.uuid5(
                RESULT_NS, f"{command.command_id}:{event}:{self.seq}",
            )),
            "Command/Result/CommandId": str(command.command_id),
            "Command/Result/Event": event,
            "Session/Id": str(self.session.pk),
            "Sim/TimeMs": sim_ms,
            "Event/TimeMs": START_EPOCH_MS + sim_ms,
            "Sim/EventSeq": self.seq,
        }
        data = command.payload.get("data", {})
        if data.get("handoff_id"):
            metrics["Command/Result/HandoffId"] = data["handoff_id"]
            metrics["Command/Result/HandoffRole"] = data["handoff_role"]
        if data.get("container_visit_id"):
            metrics["Command/Result/ContainerVisitId"] = data["container_visit_id"]
        if data.get("container_number"):
            metrics["Command/Result/ContainerNumber"] = data["container_number"]
        metrics.update(extra)
        return _apply_command_result(metrics, self.session)

    def command_for(self, che, command_type=None):
        query = EquipmentCommand.objects.filter(assignment__equipment=che).exclude(
            command_type="cancel",
        )
        if command_type:
            query = query.filter(command_type=command_type)
        return query.order_by("created_at").last()

    def business_events(self):
        return [
            event.payload["data"]["event"]
            for event in OutboxEvent.objects.filter(
                schema="scms.execution.event.v1",
            ).order_by("created_at", "event_id")
        ]

    def dcmd_metrics(self, command):
        event = OutboxEvent.objects.get(event_id=command.command_id)
        return metrics_by_name(decode_payload(bytes(event.payload_bytes)))

    def chosen_tt(self):
        return self.order.operations.get(sequence=2).assignment.equipment

    def run_h1(self, tt_ready_ms=12_000, hold_end_ms=20_000):
        dispatch_next_operation(self.order.pk)
        qc_cmd = self.command_for(self.qc)
        tt = self.chosen_tt()
        tt_cmd = self.command_for(tt)
        self.result(qc_cmd, "acknowledged", 0)
        self.result(qc_cmd, "started", 0)
        self.result(qc_cmd, "container_picked_up", 0, **{"Command/Result/Source": "VESSEL"})
        self.result(tt_cmd, "acknowledged", 0)
        self.result(tt_cmd, "started", 0)
        self.result(tt_cmd, "arrived_at_source", tt_ready_ms)
        self.result(tt_cmd, "waiting_for_handoff", tt_ready_ms)
        return qc_cmd, tt, tt_cmd

    def transfer_h1(self, qc_cmd, tt_cmd, at_ms=20_000):
        self.result(qc_cmd, "handoff_completed", at_ms)
        self.result(qc_cmd, "completed", at_ms)
        self.result(tt_cmd, "container_picked_up", at_ms)
        self.result(tt_cmd, "handoff_completed", at_ms)

    def run_happy_path(self):
        qc_cmd, tt, tt_cmd = self.run_h1()
        self.transfer_h1(qc_cmd, tt_cmd)
        dispatch_next_operation(self.order.pk)  # Q18: H2 only now
        h2_cmd = self.command_for(tt, "assign_handoff")
        rtg_cmd = self.command_for(self.rtg)
        self.result(rtg_cmd, "acknowledged", 20_000)
        self.result(rtg_cmd, "started", 20_000)
        self.result(tt_cmd, "arrived_at_destination", 80_000)
        self.result(h2_cmd, "handoff_completed", 95_000)
        self.result(rtg_cmd, "handoff_completed", 95_000)
        self.result(tt_cmd, "completed", 95_000)
        self.result(rtg_cmd, "container_dropped", 130_000, **{"Command/Result/PositionId": 112})
        self.result(rtg_cmd, "completed", 130_000)
        return tt


class PairedDispatchTests(HandoffTestBase):
    def test_qc_and_tt_are_dispatched_together_with_one_handoff_id(self):
        """B1/T-006: both DCMDs carry the same HandoffId and each other's CHE."""
        dispatch_next_operation(self.order.pk)
        qc_cmd = self.command_for(self.qc)
        tt = self.chosen_tt()
        tt_cmd = self.command_for(tt)
        qc_metrics = self.dcmd_metrics(qc_cmd)
        tt_metrics = self.dcmd_metrics(tt_cmd)
        self.assertEqual(qc_metrics["Command/Type"], "qc_discharge")
        self.assertEqual(tt_metrics["Command/Type"], "execute_order_leg")
        self.assertEqual(qc_metrics["Command/HandoffId"], tt_metrics["Command/HandoffId"])
        self.assertEqual(qc_metrics["Command/HandoffRole"], "giver")
        self.assertEqual(tt_metrics["Command/HandoffRole"], "receiver")
        self.assertEqual(qc_metrics["Command/CounterpartCheId"], tt.tos_che_id)
        self.assertEqual(tt_metrics["Command/CounterpartCheId"], 1)
        self.assertEqual(qc_metrics["Command/TransferPositionId"], 6)
        self.assertEqual(qc_metrics["Command/ContainerNumber"], CONTAINER)
        self.assertEqual(Handoff.objects.get().status, Handoff.PENDING)
        # RTG leg waits for H1 (Q18).
        self.assertEqual(
            self.order.operations.get(sequence=3).status, ExecutionOperation.PLANNED,
        )
        with self.assertRaisesMessage(ValidationError, "Q18"):
            dispatch_next_operation(self.order.pk)

    def test_command_expiry_is_on_sim_time(self):
        """TCL-028: ExpiresAtMs = anchor Sim/TimeMs + 600 s sim, not wall clock."""
        self.session.last_sim_time_ms = 5_000
        self.session.save(update_fields=["last_sim_time_ms"])
        dispatch_next_operation(self.order.pk)
        qc_cmd = self.command_for(self.qc)
        self.assertEqual(self.dcmd_metrics(qc_cmd)["Command/ExpiresAtMs"], 605_000)
        self.assertEqual(qc_cmd.expires_at_sim_ms, 605_000)

    def test_h2_expiry_is_anchored_on_the_h1_transfer(self):
        qc_cmd, tt, tt_cmd = self.run_h1()
        self.transfer_h1(qc_cmd, tt_cmd, at_ms=20_000)
        self.session.last_sim_time_ms = 999_999  # arrival-order noise must not matter
        self.session.save(update_fields=["last_sim_time_ms"])
        dispatch_next_operation(self.order.pk)
        self.assertEqual(self.command_for(self.rtg).expires_at_sim_ms, 620_000)
        self.assertEqual(self.command_for(tt, "assign_handoff").expires_at_sim_ms, 620_000)

    def test_missing_device_id_raises_instead_of_che_fallback(self):
        EquipmentState.objects.filter(pk=self.qc.pk).update(sparkplug_device_id="")
        with self.assertRaisesMessage(ValidationError, "no Sparkplug device id"):
            dispatch_next_operation(self.order.pk)


class HappyPathTests(HandoffTestBase):
    def test_qc_tt_rtg_import_completes_with_one_custody_event_per_handoff(self):
        tt = self.run_happy_path()
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.COMPLETE)
        self.assertEqual(
            list(Handoff.objects.order_by("created_at").values_list("status", flat=True)),
            [Handoff.TRANSFERRED, Handoff.TRANSFERRED],
        )
        events = self.business_events()
        self.assertEqual(events.count("container_transferred"), 2)
        self.assertEqual(events.count("container_picked_up"), 1)  # the QC appear only
        self.assertEqual(events[-2:], ["container_dropped", "leg_completed"])
        appear = OutboxEvent.objects.get(payload__data__event="container_picked_up")
        self.assertEqual(appear.payload["data"]["source"], "VESSEL")
        self.assertEqual(appear.payload["data"]["che_id"], 1)
        self.assertEqual(appear.payload["data"]["container_number"], CONTAINER)
        transferred = [
            e.payload["data"] for e in OutboxEvent.objects.filter(
                payload__data__event="container_transferred",
            ).order_by("created_at")
        ]
        self.assertEqual(
            [(t["from_che_id"], t["to_che_id"]) for t in transferred],
            [(1, tt.tos_che_id), (tt.tos_che_id, 21)],
        )
        self.assertEqual(transferred[0]["occurred_at"][:19], "2026-09-21T14:13:40")
        for che in [self.qc, tt, self.rtg]:
            che.refresh_from_db()
            self.assertTrue(che.is_available)
            self.assertFalse(che.is_dispatched)
        self.assertFalse(
            CommandResultReceipt.objects.filter(status=CommandResultReceipt.REJECTED).exists(),
        )
        informational = set(CommandResultReceipt.objects.filter(
            status=CommandResultReceipt.INFORMATIONAL,
        ).values_list("event", flat=True))
        self.assertEqual(informational, {
            "arrived_at_source", "waiting_for_handoff", "container_picked_up",
            "arrived_at_destination",
        })

    def test_repeated_and_conflicting_handoff_reports(self):
        qc_cmd, tt, tt_cmd = self.run_h1()
        self.transfer_h1(qc_cmd, tt_cmd)
        handoff = Handoff.objects.get()
        # Same Result/Id again (QoS 1 redelivery): dedupe, nothing new.
        receipt = CommandResultReceipt.objects.filter(event="handoff_completed").first()
        before = OutboxEvent.objects.count()
        _apply_command_result(receipt.raw_metrics, self.session)
        self.assertEqual(OutboxEvent.objects.count(), before)
        # A different report for the same role: flagged, never re-applied.
        rejected = self.result(qc_cmd, "handoff_completed", 21_000)
        handoff.refresh_from_db()
        self.assertEqual(rejected.status, CommandResultReceipt.REJECTED)
        self.assertEqual(rejected.reason, "handoff_conflict")
        self.assertEqual(handoff.conflict_count, 1)
        self.assertEqual(OutboxEvent.objects.count(), before)

    def test_container_number_mismatch_is_rejected(self):
        qc_cmd, tt, tt_cmd = self.run_h1()
        receipt = self.result(
            qc_cmd, "handoff_completed", 20_000,
            **{"Command/Result/ContainerNumber": "TGHU0000000"},
        )
        self.assertEqual(receipt.status, CommandResultReceipt.REJECTED)
        self.assertEqual(receipt.reason, "container_number_mismatch")
        self.assertEqual(Handoff.objects.get().status, Handoff.PENDING)

    def test_giver_drop_is_rejected_without_rolling_back_the_ddata(self):
        qc_cmd, tt, tt_cmd = self.run_h1()
        receipt = self.result(
            qc_cmd, "container_dropped", 20_000, **{"Command/Result/PositionId": 6},
        )
        self.assertEqual(receipt.status, CommandResultReceipt.REJECTED)
        self.assertEqual(receipt.reason, "handoff_giver_drop")
        self.assertTrue(CommandResultReceipt.objects.filter(pk=receipt.pk).exists())


class NoShowTests(HandoffTestBase):
    def test_tt_no_show_raises_one_exception_and_cancels_both_legs(self):
        """D-013/T-007: handoff_timed_out → NO_TT_TIMEOUT, cancel DCMDs, handoff_exception."""
        qc_cmd, tt, tt_cmd = self.run_h1(tt_ready_ms=10**9)  # TT never ready
        self.result(qc_cmd, "waiting_for_handoff", 20_000)
        receipt = self.result(qc_cmd, "handoff_timed_out", 320_000)
        self.assertEqual(receipt.status, CommandResultReceipt.APPLIED)

        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.EXCEPTION)
        self.assertEqual(handoff.exception_reason, "NO_TT_TIMEOUT")
        self.assertEqual(handoff.exception_event_ms, START_EPOCH_MS + 320_000)
        cancels = EquipmentCommand.objects.filter(command_type="cancel")
        self.assertEqual(
            sorted(self.dcmd_metrics(c)["Command/CancelCommandId"] for c in cancels),
            sorted([str(qc_cmd.command_id), str(tt_cmd.command_id)]),
        )
        for cancel in cancels:
            metrics = self.dcmd_metrics(cancel)
            self.assertEqual(metrics["Command/Type"], "cancel")
            self.assertTrue(metrics["Command/AssignmentId"])
            self.assertEqual(metrics["Command/ExpiresAtMs"], 920_000)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.CANCELLED)
        self.assertEqual(self.order.failure_reason, "NO_TT_TIMEOUT")
        self.assertEqual(
            set(self.order.operations.values_list("status", flat=True)),
            {ExecutionOperation.CANCELLED},
        )
        for che in [self.qc, tt]:
            che.refresh_from_db()
            self.assertTrue(che.is_available)
            self.assertEqual(che.idle_since_event_ms, START_EPOCH_MS + 320_000)
        exception = OutboxEvent.objects.get(payload__data__event="handoff_exception")
        data = exception.payload["data"]
        self.assertEqual(data["reason"], "NO_TT_TIMEOUT")
        self.assertEqual(data["container_number"], CONTAINER)
        self.assertEqual(data["cancelled_leg_ids"], [4101, 4102, 4103])
        self.assertEqual(data["destination_position_id"], 112)
        self.assertEqual(exception.payload["terminal"]["id"], 1)

        count = OutboxEvent.objects.count()
        # Duplicate time-out (new Result/Id): a no-op.
        repeat = self.result(qc_cmd, "handoff_timed_out", 320_000)
        self.assertEqual(repeat.status, CommandResultReceipt.INFORMATIONAL)
        # Godot confirms the cancels: idempotent, no new events.
        self.result(qc_cmd, "cancelled", 320_100)
        self.result(tt_cmd, "cancelled", 320_100)
        # Late completion after the exception: rejected and logged.
        with self.assertLogs("execution.services.handoffs", level="WARNING"):
            late = self.result(tt_cmd, "handoff_completed", 330_000)
        self.assertEqual(late.status, CommandResultReceipt.REJECTED)
        self.assertIn(late.reason, {"handoff_not_pending", "A terminal command cannot transition to another result."})
        self.assertEqual(OutboxEvent.objects.count(), count)
        self.assertEqual(Handoff.objects.get().status, Handoff.EXCEPTION)

    def test_timed_out_after_transfer_is_rejected(self):
        qc_cmd, tt, tt_cmd = self.run_h1()
        self.result(qc_cmd, "handoff_completed", 20_000)
        receipt = self.result(qc_cmd, "handoff_timed_out", 320_000)
        self.assertEqual(receipt.status, CommandResultReceipt.REJECTED)
        self.assertEqual(Handoff.objects.get().status, Handoff.TRANSFERRED)

    def test_operator_cancel_via_api_queues_a_cancel_dcmd(self):
        """B10/TCL-028: an OES cancel tells the CHE."""
        dispatch_next_operation(self.order.pk)
        qc_cmd = self.command_for(self.qc)
        with self.settings(OES_API_TOKEN="t"):
            response = APIClient().post(
                reverse("equipment-command-event", args=[qc_cmd.command_id]),
                {"event": "cancelled", "reason": "operator"}, format="json",
                HTTP_X_OES_TOKEN="t",
            )
        self.assertEqual(response.status_code, 200, response.content)
        targets = {
            self.dcmd_metrics(cancel)["Command/CancelCommandId"]
            for cancel in EquipmentCommand.objects.filter(command_type="cancel")
        }
        # The QC gets its cancel and, per TL review B-2, so does the paired TT.
        tt_cmd = self.command_for(self.chosen_tt())
        self.assertEqual(targets, {str(qc_cmd.command_id), str(tt_cmd.command_id)})


class TtSelectionTests(HandoffTestBase):
    tt_ids = (11, 12, 13)

    def test_d014_idle_longest_on_event_time(self):
        EquipmentState.objects.filter(tos_che_id=11).update(idle_since_event_ms=START_EPOCH_MS + 9_000)
        EquipmentState.objects.filter(tos_che_id=12).update(idle_since_event_ms=START_EPOCH_MS + 3_000)
        EquipmentState.objects.filter(tos_che_id=13).update(idle_since_event_ms=START_EPOCH_MS + 5_000)
        # Wall-clock telemetry age must not matter.
        EquipmentState.objects.filter(tos_che_id=11).update(
            last_seen_at=timezone.now() - timezone.timedelta(seconds=30),
        )
        dispatch_next_operation(self.order.pk)
        self.assertEqual(self.chosen_tt().tos_che_id, 12)

    def test_tie_break_is_seeded_from_sim_seed_and_order_id(self):
        candidates = list(EquipmentState.objects.filter(equipment_type_id=TT_TYPE))
        picks = {
            (seed, order_id): select_equipment(candidates, seed=seed, order_id=order_id).tos_che_id
            for seed in range(1, 6) for order_id in (41, 42, 43)
        }
        # Same inputs, any candidate order → same pick.
        for (seed, order_id), che_id in picks.items():
            self.assertEqual(
                select_equipment(list(reversed(candidates)), seed=seed, order_id=order_id).tos_che_id,
                che_id,
            )
        # The draw really depends on the inputs (not always the lowest id).
        self.assertGreater(len(set(picks.values())), 1)

    def test_dbirth_sets_idle_since_from_session_start(self):
        che = self.che(14, "TT04", TT_TYPE, "TT")
        self.assertIsNone(che.idle_since_event_ms)
        process_sparkplug_message(
            "spBv1.0/ENTERPRISE_A/DBIRTH/SCMS_ENTERPRISE_A_SITE_A/TT04",
            encode_payload([
                {"name": "Identity/Id", "type": "Int64", "value": 14},
                {"name": "State/PoweredOn", "type": "Boolean", "value": True},
            ], sequence=1),
        )
        che.refresh_from_db()
        self.assertEqual(che.idle_since_event_ms, START_EPOCH_MS)


class SessionAndDedupeTests(HandoffTestBase):
    def test_ddata_from_another_session_is_dropped(self):
        """B5: a late replay from a previous session never touches this one."""
        dispatch_next_operation(self.order.pk)
        qc_cmd = self.command_for(self.qc)
        process_sparkplug_message(
            "spBv1.0/ENTERPRISE_A/DDATA/SCMS_ENTERPRISE_A_SITE_A/QC01",
            encode_payload([
                {"name": "Session/Id", "type": "String", "value": str(uuid.uuid4())},
                {"name": "Command/Result/Id", "type": "String", "value": str(uuid.uuid4())},
                {"name": "Command/Result/CommandId", "type": "String",
                 "value": str(qc_cmd.command_id)},
                {"name": "Command/Result/Event", "type": "String", "value": "started"},
            ], sequence=1),
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.stale_message_count, 1)
        self.assertFalse(CommandResultReceipt.objects.exists())

    def test_result_is_applied_once_and_records_sim_fields(self):
        dispatch_next_operation(self.order.pk)
        qc_cmd = self.command_for(self.qc)
        receipt = self.result(qc_cmd, "acknowledged", 1_500)
        again = _apply_command_result(receipt.raw_metrics, self.session)
        self.assertEqual(again.pk, receipt.pk)
        self.assertEqual(CommandResultReceipt.objects.count(), 1)
        self.assertEqual(receipt.sim_time_ms, 1_500)
        self.assertEqual(receipt.simulation_session, self.session)
        self.session.refresh_from_db()


class DeterminismTests(HandoffTestBase):
    tt_ids = (11, 12, 13)

    def trace(self, tt_creation_order):
        """Run the happy path and record (states, events, chosen TT, HandoffIds)."""
        EquipmentState.objects.filter(equipment_type_id=TT_TYPE).delete()
        for che_id in tt_creation_order:
            self.che(che_id, f"TT{che_id - 10:02d}", TT_TYPE, "TT")
        states = []

        def snap():
            self.order.refresh_from_db()
            states.append((
                self.order.status,
                tuple(self.order.operations.order_by("sequence").values_list("status", flat=True)),
                tuple(Handoff.objects.order_by("created_at").values_list("status", flat=True)),
            ))

        original = self.result

        def recording_result(*args, **kwargs):
            receipt = original(*args, **kwargs)
            snap()
            return receipt

        self.result = recording_result
        try:
            tt = self.run_happy_path()
        finally:
            self.result = original
        return {
            "states": states,
            "events": self.business_events(),
            "tt": tt.tos_che_id,
            "handoffs": list(Handoff.objects.order_by("created_at").values_list("pk", flat=True)),
            "transferred_at": [
                e.payload["data"]["occurred_at"] for e in OutboxEvent.objects.filter(
                    payload__data__event="container_transferred",
                ).order_by("created_at")
            ],
        }

    def run_isolated(self, tt_creation_order):
        result = {}
        try:
            with transaction.atomic():
                self.seq = 0
                result = self.trace(tt_creation_order)
                raise _Rollback
        except _Rollback:
            pass
        return result

    def test_d016_same_seed_same_states_in_the_same_order(self):
        """D-016: rerun → same states, same order, same TT, same HandoffIds/event times."""
        first = self.run_isolated((11, 12, 13))
        second = self.run_isolated((13, 11, 12))  # DB row order must not matter
        self.assertTrue(first["states"])
        self.assertEqual(first, second)


class SimTimeExpiryTests(HandoffTestBase):
    """TCL-028 / TCL-014a 20fce6b: ExpiresAtMs = latest Sim/TimeMs + TTL (sim ms)."""

    topic = "spBv1.0/ENTERPRISE_A/{}/SCMS_ENTERPRISE_A_SITE_A{}"

    def nbirth(self, extra=()):
        process_sparkplug_message(self.topic.format("NBIRTH", ""), encode_payload([
            {"name": "bdSeq", "type": "Int64", "value": 1},
            {"name": "Session/Id", "type": "String", "value": str(self.session.pk)},
            {"name": "Site/Id", "type": "String", "value": str(self.site.source_id)},
            {"name": "Configuration/Revision", "type": "Int64", "value": 1},
            {"name": "Configuration/Checksum", "type": "String", "value": "a" * 64},
            *extra,
        ], sequence=0))
        self.session.refresh_from_db()
        # NBIRTH invalidates devices until their DBIRTH; re-bind them for dispatch.
        EquipmentState.objects.filter(site=self.site).update(
            device_connected=True, data_valid=True,
        )

    def test_periodic_ddata_sim_time_is_the_expiry_anchor(self):
        self.nbirth()
        process_sparkplug_message(self.topic.format("DDATA", "/TT01"), encode_payload([
            {"name": "Session/Id", "type": "String", "value": str(self.session.pk)},
            {"name": "Sim/TimeMs", "type": "Int64", "value": 42_000},
            {"name": "State/Working", "type": "Boolean", "value": False},
        ], sequence=1))
        # An older Sim/TimeMs (late DDATA) never moves the clock back.
        process_sparkplug_message(self.topic.format("DDATA", "/TT02"), encode_payload([
            {"name": "Session/Id", "type": "String", "value": str(self.session.pk)},
            {"name": "Sim/TimeMs", "type": "Int64", "value": 41_000},
        ], sequence=2))
        self.session.refresh_from_db()
        self.assertEqual(self.session.last_sim_time_ms, 42_000)
        dispatch_next_operation(self.order.pk)
        expires = self.dcmd_metrics(self.command_for(self.qc))["Command/ExpiresAtMs"]
        self.assertEqual(expires, 42_000 + 600_000)
        # Not a wall-clock epoch value (which Godot would read as ~55 years ahead).
        self.assertLess(expires, 10**10)

    def test_ttl_comes_from_nbirth_with_600_s_fallback(self):
        self.nbirth([{"name": "Sim/Timing/CommandTtlS", "type": "Int64", "value": 90}])
        self.assertEqual(self.session.command_ttl_s, 90)
        dispatch_next_operation(self.order.pk)
        self.assertEqual(self.command_for(self.qc).expires_at_sim_ms, 90_000)

        self.nbirth()  # a rebirth without the metric falls back to 600 s
        self.assertIsNone(self.session.command_ttl_s)
        from .services.dispatch import command_expiry_sim_ms
        self.assertEqual(command_expiry_sim_ms(self.session, 1_000), 601_000)


class ClaimEnvironmentTests(HandoffTestBase):
    """TCL-028: OES forwards the SIMULATOR_* sim settings; the runner allowlists them."""

    def runner_allowlist(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[2] / "simulator-runner" / "runner.py"
        if not path.exists():
            # TL review S-2: the OES Docker build context has no runner source;
            # the runner side is covered by apps/simulator-runner/test_runner.py.
            self.skipTest("simulator-runner source not available in this context")
        spec = importlib.util.spec_from_file_location("scms_simulator_runner", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ALLOWED_ENVIRONMENT

    def test_sim_settings_are_in_the_claim_environment(self):
        from django.conf import settings as django_settings

        from .services.simulations import launch_environment

        self.session.seed = None
        self.session.start_epoch_ms = None
        configured = {
            "SIMULATOR_SEED": "7",
            "SIMULATOR_FIXED_STEP_MS": "50",
            "SIMULATOR_TT_NO_SHOW_TIMEOUT_S": "300",
            "SIMULATOR_DEVICE_ID_MODE": "name",
            "SIMULATOR_NOT_A_SETTING": "x",
        }
        with self.settings(SIMULATOR_SIM_SETTINGS=configured):
            environment = launch_environment(self.session, "placeholder-token")
        self.assertEqual(environment["SIMULATOR_SEED"], "7")
        self.assertEqual(environment["SIMULATOR_FIXED_STEP_MS"], "50")
        self.assertEqual(environment["SIMULATOR_TT_NO_SHOW_TIMEOUT_S"], "300")
        self.assertEqual(environment["SIMULATOR_DEVICE_ID_MODE"], "name")
        self.assertEqual(environment["SIMULATOR_COMMAND_TTL_S"], "600")
        self.assertEqual(environment["SIMULATOR_RUN_ID"], str(self.session.pk))
        self.assertNotIn("SIMULATOR_NOT_A_SETTING", environment)
        # A rerun reuses the session's seed and start epoch (D-016).
        self.session.seed = SEED
        self.session.start_epoch_ms = START_EPOCH_MS
        with self.settings(SIMULATOR_SIM_SETTINGS=configured):
            environment = launch_environment(self.session, "placeholder-token")
        self.assertEqual(environment["SIMULATOR_SEED"], str(SEED))
        self.assertEqual(environment["SIMULATOR_START_EPOCH_MS"], str(START_EPOCH_MS))
        # Every name OES can send passes the runner's closed allowlist.
        allowed = self.runner_allowlist()
        self.assertLessEqual(set(django_settings.SIMULATOR_SIM_SETTING_NAMES), allowed)
        self.assertLessEqual(set(environment), allowed)


class RerunAcrossSessionsTests(HandoffTestBase):
    """TL review B-1: two sessions back to back on the same CHE rows, no rollback."""

    tt_ids = (11, 12, 13)

    def birth(self, session):
        process_sparkplug_message(
            f"spBv1.0/{session.sparkplug_group_id}/NBIRTH/{session.sparkplug_node_id}",
            encode_payload([
                {"name": "bdSeq", "type": "Int64", "value": 1},
                {"name": "Session/Id", "type": "String", "value": str(session.pk)},
                {"name": "Site/Id", "type": "String", "value": str(self.site.source_id)},
                {"name": "Configuration/Revision", "type": "Int64", "value": 1},
                {"name": "Configuration/Checksum", "type": "String", "value": "a" * 64},
                {"name": "Sim/Seed", "type": "Int64", "value": SEED},
                {"name": "Sim/StartEpochMs", "type": "Int64", "value": START_EPOCH_MS},
            ], sequence=0),
        )
        EquipmentState.objects.filter(site=self.site).update(
            device_connected=True, data_valid=True,
        )
        session.refresh_from_db()
        return session

    def test_second_session_starts_with_every_che_idle_since_its_start(self):
        self.birth(self.session)
        first_tt = self.run_happy_path().tos_che_id
        first_states = self.business_events()
        tts = list(EquipmentState.objects.filter(equipment_type_id=TT_TYPE))
        self.assertGreater(
            EquipmentState.objects.get(tos_che_id=first_tt).idle_since_event_ms, START_EPOCH_MS,
        )
        # An order id whose fresh-run draw is the TT run 1 used: without the
        # per-session reset that TT looks "least idle" and cannot be picked.
        order_id = next(
            candidate for candidate in range(42, 200)
            if select_equipment(
                [EquipmentState(tos_che_id=t.tos_che_id) for t in tts],
                seed=SEED, order_id=candidate,
            ).tos_che_id == first_tt
        )

        SimulationSession.objects.filter(pk=self.session.pk).update(
            actual_state=SimulationSession.STOPPED,
        )
        self.session = SimulationSession.objects.create(
            site=self.site, configuration_revision=1, configuration_checksum="a" * 64,
            sparkplug_group_id="ENTERPRISE_A", sparkplug_node_id="SCMS_ENTERPRISE_A_SITE_A",
            actual_state=SimulationSession.RUNNING, node_connected=True,
        )
        self.birth(self.session)
        self.assertEqual(
            set(EquipmentState.objects.filter(site=self.site).values_list(
                "idle_since_event_ms", flat=True,
            )),
            {START_EPOCH_MS},
        )
        self.order = self.release(order_id)
        before = OutboxEvent.objects.filter(schema="scms.execution.event.v1").count()
        second_tt = self.run_happy_path().tos_che_id
        self.assertEqual(second_tt, first_tt)
        second_states = self.business_events()[before:]
        self.assertEqual(second_states, first_states)



class HandoffAbortTests(HandoffTestBase):
    """TL review B-2: a pending hand-off never outlives a failed/cancelled leg or its session."""

    def cancel_targets(self):
        return sorted(
            self.dcmd_metrics(cancel)["Command/CancelCommandId"]
            for cancel in EquipmentCommand.objects.filter(command_type="cancel")
        )

    def assert_free(self, *ches):
        for che in ches:
            che.refresh_from_db()
            self.assertTrue(che.is_available, che.name)
            self.assertFalse(che.is_dispatched, che.name)

    def test_qc_failed_cancels_handoff_and_tt_leg(self):
        qc_cmd, tt, tt_cmd = self.run_h1()
        receipt = self.result(qc_cmd, "failed", 15_000,
                              **{"Command/Result/Reason": "spreader fault"})
        self.assertEqual(receipt.status, CommandResultReceipt.APPLIED)
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.CANCELLED)
        self.assertEqual(handoff.exception_reason, "LEG_FAILED")
        self.assertEqual(self.cancel_targets(), [str(tt_cmd.command_id)])
        tt_cmd.refresh_from_db()
        self.assertEqual(tt_cmd.status, EquipmentCommand.CANCELLED)
        self.assertEqual(
            self.order.operations.get(sequence=2).status, ExecutionOperation.CANCELLED,
        )
        self.assert_free(self.qc, tt)
        # Both CHEs are dispatchable again for the next order.
        self.assertIsNotNone(dispatch_next_operation(self.release(42).pk))

    def test_operator_cancel_of_tt_leg_cancels_the_qc(self):
        qc_cmd, tt, tt_cmd = self.run_h1()
        from .services.dispatch import apply_command_event

        apply_command_event(tt_cmd.pk, "cancelled", reason="operator", notify_che=True)
        self.assertEqual(Handoff.objects.get().status, Handoff.CANCELLED)
        self.assertEqual(
            self.cancel_targets(), sorted([str(qc_cmd.command_id), str(tt_cmd.command_id)]),
        )
        self.assert_free(self.qc, tt)

    def test_rejected_assign_handoff_cancels_tt_leg_and_rtg(self):
        """Godot TCL-014a rejects assign_handoff: nothing may stay live or locked."""
        qc_cmd, tt, tt_cmd = self.run_h1()
        self.transfer_h1(qc_cmd, tt_cmd)
        dispatch_next_operation(self.order.pk)
        h2_cmd = self.command_for(tt, "assign_handoff")
        rtg_cmd = self.command_for(self.rtg)
        self.result(h2_cmd, "failed", 20_000,
                    **{"Command/Result/Reason": "Unsupported command type: assign_handoff"})
        h2 = Handoff.objects.order_by("created_at").last()
        self.assertEqual(h2.status, Handoff.CANCELLED)
        # The TT's still-running leg command and the RTG leg both get a cancel DCMD.
        self.assertEqual(
            self.cancel_targets(), sorted([str(tt_cmd.command_id), str(rtg_cmd.command_id)]),
        )
        self.assertEqual(
            self.order.operations.get(sequence=3).status, ExecutionOperation.CANCELLED,
        )
        self.assert_free(tt, self.rtg)

    def test_new_session_closes_handoffs_of_an_abandoned_one(self):
        self.run_h1()
        old_session = self.session
        SimulationSession.objects.filter(pk=old_session.pk).update(
            actual_state=SimulationSession.FAILED,
        )
        self.session = SimulationSession.objects.create(
            site=self.site, configuration_revision=1, configuration_checksum="a" * 64,
            sparkplug_group_id="ENTERPRISE_A", sparkplug_node_id="SCMS_ENTERPRISE_A_SITE_A",
            actual_state=SimulationSession.RUNNING, node_connected=True,
        )
        process_sparkplug_message(
            "spBv1.0/ENTERPRISE_A/NBIRTH/SCMS_ENTERPRISE_A_SITE_A",
            encode_payload([
                {"name": "bdSeq", "type": "Int64", "value": 1},
                {"name": "Session/Id", "type": "String", "value": str(self.session.pk)},
                {"name": "Site/Id", "type": "String", "value": str(self.site.source_id)},
                {"name": "Configuration/Revision", "type": "Int64", "value": 1},
                {"name": "Configuration/Checksum", "type": "String", "value": "a" * 64},
                {"name": "Sim/Seed", "type": "Int64", "value": SEED},
            ], sequence=0),
        )
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.CANCELLED)
        self.assertEqual(handoff.exception_reason, "SESSION_ABANDONED")
        EquipmentState.objects.filter(site=self.site).update(
            device_connected=True, data_valid=True, is_available=True, is_dispatched=False,
            working=False,
        )
        self.assertIsNotNone(dispatch_next_operation(self.release(42).pk))

    def test_stop_and_runner_failure_close_pending_handoffs(self):
        from .services.dispatch import _pending_handoff_for
        from .services.simulations import stop_simulation

        self.run_h1()
        self.assertTrue(_pending_handoff_for(self.qc, self.session))
        stop_simulation(self.session.pk)
        handoff = Handoff.objects.get()
        self.assertEqual(handoff.status, Handoff.CANCELLED)
        self.assertEqual(handoff.exception_reason, "SESSION_STOPPED")
        self.assertFalse(_pending_handoff_for(self.qc, self.session))

    def test_busy_check_is_scoped_to_the_session(self):
        from .services.dispatch import _pending_handoff_for

        self.run_h1()
        other = SimulationSession.objects.create(
            site=self.site, configuration_revision=1, configuration_checksum="a" * 64,
            sparkplug_group_id="ENTERPRISE_A", sparkplug_node_id="OTHER",
            actual_state=SimulationSession.STOPPED,
        )
        self.assertTrue(_pending_handoff_for(self.qc, self.session))
        self.assertFalse(_pending_handoff_for(self.qc, other))



class SimClockFallbackTests(HandoffTestBase):
    """TL review S-3 / S-6."""

    def test_result_without_event_time_uses_the_session_sim_clock(self):
        from .services.dispatch import apply_command_event

        SimulationSession.objects.filter(pk=self.session.pk).update(
            start_epoch_ms=START_EPOCH_MS, last_sim_time_ms=42_000,
        )
        qc_cmd, tt, tt_cmd = self.run_h1()
        apply_command_event(tt_cmd.pk, "cancelled", reason="operator", notify_che=True)
        for che in (tt, self.qc):
            che.refresh_from_db()
            self.assertEqual(che.idle_since_event_ms, START_EPOCH_MS + 42_000, che.name)

    def test_missing_seed_is_an_error_when_a_tie_must_be_broken(self):
        from django.core.exceptions import ValidationError

        candidates = [EquipmentState(tos_che_id=i) for i in (11, 12)]
        with self.assertRaises(ValidationError):
            select_equipment(candidates, seed=None, order_id=41)
        # No tie, no draw: a missing seed is harmless.
        candidates[1].idle_since_event_ms = 5
        self.assertEqual(select_equipment(candidates, seed=None, order_id=41).tos_che_id, 11)
