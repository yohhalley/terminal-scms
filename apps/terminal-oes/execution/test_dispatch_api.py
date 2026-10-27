"""TCL-028: ``POST /api/orders/{id}/dispatch-next/`` and Godot failure results (§3.2c).

The endpoint dispatches the next step of a released order as TCL-002 DCMDs
(QC ``qc_discharge`` + TT ``execute_order_leg`` paired on one HandoffId, then
TT→RTG after H1 transferred). A re-call while the step is in flight is a no-op.
Results flow back through the Sparkplug result path into execution state, and
custody moves to the TT with ``container_transferred`` (MiniTOS
``transfer_container`` makes the visit On Equipment / TT).
"""

from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from .models import (
    CommandResultReceipt,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    Handoff,
    OutboxEvent,
    SimulationSession,
)
from .services.simulations import stop_simulation
from .services.sparkplug_runtime import process_sparkplug_message
from .sparkplug import encode_payload
from .test_handoffs import CONTAINER, HandoffTestBase

TOKEN = "t"


class DispatchNextApiTestBase(HandoffTestBase):
    def post(self, order_pk, body=None, token=TOKEN):
        headers = {"HTTP_X_OES_TOKEN": token} if token else {}
        with self.settings(OES_API_TOKEN=TOKEN):
            return APIClient().post(
                reverse("execution-order-dispatch-next", args=[order_pk]),
                body or {}, format="json", **headers,
            )

    def dcmd_count(self):
        return OutboxEvent.objects.filter(topic__contains="/DCMD/").count()

    def leg_commands(self):
        return EquipmentCommand.objects.exclude(command_type="cancel")


class DispatchNextApiTests(DispatchNextApiTestBase):
    def test_same_token_auth_as_the_other_oes_endpoints(self):
        self.assertIn(self.post(self.order.pk, token=None).status_code, {401, 403})
        self.assertIn(self.post(self.order.pk, token="wrong").status_code, {401, 403})
        self.assertFalse(EquipmentCommand.objects.exists())

    def test_first_call_turns_qc_and_tt_operations_into_paired_dcmds(self):
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.data["dispatched"])
        self.assertEqual(response.data["status"], ExecutionOrder.IN_PROGRESS)
        self.assertEqual(self.dcmd_count(), 2)
        qc_cmd = self.command_for(self.qc)
        tt_cmd = self.command_for(self.chosen_tt())
        self.assertEqual(self.dcmd_metrics(qc_cmd)["Command/Type"], "qc_discharge")
        self.assertEqual(self.dcmd_metrics(tt_cmd)["Command/Type"], "execute_order_leg")
        self.assertEqual(
            self.dcmd_metrics(qc_cmd)["Command/HandoffId"],
            self.dcmd_metrics(tt_cmd)["Command/HandoffId"],
        )
        statuses = dict(self.order.operations.values_list("sequence", "status"))
        self.assertEqual(statuses, {
            1: ExecutionOperation.DISPATCHED, 2: ExecutionOperation.DISPATCHED,
            3: ExecutionOperation.PLANNED,
        })

    def test_recall_while_in_flight_is_an_idempotent_noop(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        commands, dcmds = self.leg_commands().count(), self.dcmd_count()
        for _ in range(2):
            response = self.post(self.order.pk)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertFalse(response.data["dispatched"])
            self.assertEqual(response.data["code"], "waiting_for_handoff")
            self.assertEqual(response.data["id"], self.order.pk)
        self.assertEqual(self.leg_commands().count(), commands)
        self.assertEqual(self.dcmd_count(), dcmds)
        self.assertEqual(Handoff.objects.count(), 1)

    def test_operation_not_plannable_is_409(self):
        # TL review N-3: a blocked next leg cannot be dispatched.
        self.order.operations.filter(sequence=1).update(status=ExecutionOperation.BLOCKED)
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["code"], "operation_not_plannable")
        self.assertTrue(response.data["detail"])
        self.assertFalse(EquipmentCommand.objects.exists())

    def test_uncoded_service_error_is_409_dispatch_refused(self):
        # TL review N-3: a ValidationError without a code maps to the fallback.
        with mock.patch(
            "execution.views.dispatch_next_operation",
            side_effect=ValidationError("Something the service refused."),
        ):
            response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data, {
            "code": "dispatch_refused", "detail": "Something the service refused.",
        })

    def test_not_released_order_is_409(self):
        for state in [ExecutionOrder.COMPLETE, ExecutionOrder.FAILED, ExecutionOrder.CANCELLED]:
            ExecutionOrder.objects.filter(pk=self.order.pk).update(status=state)
            response = self.post(self.order.pk)
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.data["code"], "order_not_released")
            self.assertTrue(response.data["detail"])
        self.assertFalse(EquipmentCommand.objects.exists())

    def test_unknown_order_is_404(self):
        self.assertEqual(self.post(999_999).status_code, 404)

    def test_nothing_left_to_dispatch_is_409(self):
        ExecutionOrder.objects.filter(pk=self.order.pk).update(status=ExecutionOrder.IN_PROGRESS)
        self.order.operations.update(status=ExecutionOperation.COMPLETE)
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["code"], "nothing_to_dispatch")

    def test_no_che_or_no_simulation_is_409_and_leaves_the_order_released(self):
        EquipmentState.objects.filter(equipment_type_code="TT").update(is_available=False)
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["code"], "no_equipment")
        SimulationSession.objects.update(node_connected=False)
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["code"], "no_simulation")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.RELEASED)
        self.assertFalse(EquipmentCommand.objects.exists())
        self.assertFalse(Handoff.objects.exists())

    def test_bad_equipment_id_is_400(self):
        self.assertEqual(self.post(self.order.pk, {"equipment_id": 0}).status_code, 400)
        response = self.post(self.order.pk, {"equipment_id": self.rtg.pk})  # wrong type for QC
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data["code"], "equipment_invalid")
        self.assertFalse(EquipmentCommand.objects.exists())

    def test_bad_body_is_400_with_code_detail_and_drf_errors(self):
        # QA M1: same {code, detail} shape as every refusal, plus DRF errors.
        for body, field_error in (
            ({"equipment_id": "abc"}, "A valid integer is required."),
            ({"equipment_id": 0}, "Ensure this value is greater than or equal to 1."),
        ):
            response = self.post(self.order.pk, body)
            self.assertEqual(response.status_code, 400, response.content)
            self.assertEqual(set(response.data), {"code", "detail", "errors"})
            self.assertEqual(response.data["code"], "invalid_request")
            self.assertEqual(
                response.data["detail"], f"Invalid request body: equipment_id: {field_error}",
            )
            self.assertEqual(response.data["errors"], {"equipment_id": [field_error]})
        self.assertFalse(EquipmentCommand.objects.exists())

    def test_unknown_order_with_bad_body_is_404(self):
        # QA M1: the order is looked up before the body is validated.
        self.assertEqual(self.post(999_999, {"equipment_id": "abc"}).status_code, 404)

    def test_end_to_end_results_drive_execution_state_and_custody_to_the_tt(self):
        """API dispatch → Godot results → OES state → container_transferred to the TT."""
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd = self.command_for(self.qc)
        tt = self.chosen_tt()
        tt_cmd = self.command_for(tt)
        for cmd in (qc_cmd, tt_cmd):
            self.result(cmd, "acknowledged", 0)
            self.result(cmd, "started", 0)
        self.result(qc_cmd, "container_picked_up", 0, **{"Command/Result/Source": "VESSEL"})
        statuses = dict(self.order.operations.values_list("sequence", "status"))
        self.assertEqual(statuses[1], ExecutionOperation.WORKING)
        self.assertEqual(statuses[2], ExecutionOperation.WORKING)
        self.transfer_h1(qc_cmd, tt_cmd, at_ms=20_000)

        self.assertEqual(Handoff.objects.get().status, Handoff.TRANSFERRED)
        self.assertEqual(
            self.order.operations.get(sequence=1).status, ExecutionOperation.COMPLETE,
        )
        transferred = OutboxEvent.objects.get(payload__data__event="container_transferred")
        data = transferred.payload["data"]
        # The fields MiniTOS apply_execution_event needs to move custody to the TT.
        self.assertEqual((data["from_che_id"], data["to_che_id"]), (1, tt.tos_che_id))
        self.assertEqual(data["container_number"], CONTAINER)
        self.assertTrue(data["handoff_id"])
        appear = OutboxEvent.objects.get(payload__data__event="container_picked_up")
        self.assertEqual(appear.payload["data"]["source"], "VESSEL")

        # Now the TT→RTG step is due: the next call dispatches it, a re-call is a no-op.
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.dcmd_metrics(self.command_for(tt, "assign_handoff"))[
            "Command/Type"], "assign_handoff")
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.data["dispatched"])
        # Every leg is dispatched or done; the live TT/RTG legs make it a no-op.
        self.assertEqual(response.data["code"], "nothing_to_dispatch")
        self.assertFalse(
            CommandResultReceipt.objects.filter(status=CommandResultReceipt.REJECTED).exists(),
        )


class DispatchNextInFlightCodeTests(DispatchNextApiTestBase):
    """QA M3: the other in-flight codes on the 200 (leg live) and 409 (no leg live) paths."""

    def through_h1_and_h2(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd, tt_cmd = self.command_for(self.qc), self.command_for(self.chosen_tt())
        for cmd in (qc_cmd, tt_cmd):
            self.result(cmd, "acknowledged", 0)
            self.result(cmd, "started", 0)
        self.transfer_h1(qc_cmd, tt_cmd)
        self.assertEqual(self.post(self.order.pk).status_code, 201)  # TT→RTG (H2)

    def assert_noop(self, code):
        counts = (self.leg_commands().count(), self.dcmd_count(), Handoff.objects.count())
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.data["dispatched"])
        self.assertEqual(response.data["code"], code)
        self.assertTrue(response.data["detail"])
        self.assertEqual(
            (self.leg_commands().count(), self.dcmd_count(), Handoff.objects.count()), counts,
        )

    def test_already_dispatched_with_a_live_leg_is_200(self):
        self.through_h1_and_h2()
        # The RTG leg reads as planned again while H2 exists (e.g. a manual edit).
        self.order.operations.filter(sequence=3).update(status=ExecutionOperation.PLANNED)
        self.assert_noop("already_dispatched")

    def test_earlier_operation_incomplete_with_a_live_leg_is_200(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        # The TT leg reads as complete while the QC leg is still dispatched.
        self.order.operations.filter(sequence=2).update(status=ExecutionOperation.COMPLETE)
        self.assert_noop("earlier_operation_incomplete")

    def test_earlier_operation_incomplete_without_a_live_leg_is_409(self):
        self.order.operations.filter(sequence=1).update(status=ExecutionOperation.CANCELLED)
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["code"], "earlier_operation_incomplete")
        self.assertEqual(set(response.data), {"code", "detail"})
        self.assertFalse(EquipmentCommand.objects.exists())

    def test_already_dispatched_without_a_live_leg_is_409(self):
        # Not reachable from real state (the code needs an active giver leg),
        # so the service refusal is mocked to check the view's mapping.
        with mock.patch(
            "execution.views.dispatch_next_operation",
            side_effect=ValidationError("already sent", code="already_dispatched"),
        ):
            response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data, {"code": "already_dispatched", "detail": "already sent"})


class DispatchNextEquipmentInvalidTests(DispatchNextApiTestBase):
    """QA M3: every way an explicit ``equipment_id`` is refused is 400 equipment_invalid."""

    def assert_equipment_invalid(self, equipment_id):
        counts = (self.leg_commands().count(), self.dcmd_count(), Handoff.objects.count())
        response = self.post(self.order.pk, {"equipment_id": equipment_id})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data["code"], "equipment_invalid")
        self.assertTrue(response.data["detail"])
        self.assertEqual(
            (self.leg_commands().count(), self.dcmd_count(), Handoff.objects.count()), counts,
        )

    def test_unknown_equipment_id(self):
        self.assert_equipment_invalid(999_999)

    def test_right_type_but_unavailable(self):
        EquipmentState.objects.filter(pk=self.qc.pk).update(is_available=False)
        self.assert_equipment_invalid(self.qc.pk)

    def test_right_type_but_busy(self):
        EquipmentState.objects.filter(pk=self.qc.pk).update(is_dispatched=True)
        self.assert_equipment_invalid(self.qc.pk)

    def test_che_of_another_simulation_session(self):
        old = SimulationSession.objects.create(
            site=self.site, configuration_revision=1, configuration_checksum="a" * 64,
            sparkplug_group_id="ENTERPRISE_A", sparkplug_node_id="SCMS_ENTERPRISE_A_SITE_A",
            actual_state=SimulationSession.STOPPED, desired_state=SimulationSession.STOPPED_DESIRED,
        )
        EquipmentState.objects.filter(pk=self.qc.pk).update(simulation_session=old)
        self.assert_equipment_invalid(self.qc.pk)

    def test_chained_receiver_of_the_wrong_type(self):
        # TT→RTG step: the named CHE is for the receiver (RTG) leg.
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd, tt_cmd = self.command_for(self.qc), self.command_for(self.chosen_tt())
        for cmd in (qc_cmd, tt_cmd):
            self.result(cmd, "acknowledged", 0)
            self.result(cmd, "started", 0)
        self.transfer_h1(qc_cmd, tt_cmd)
        other_tt = next(tt for tt in self.tts if tt.pk != self.chosen_tt().pk)
        self.assert_equipment_invalid(other_tt.pk)
        self.assertFalse(EquipmentCommand.objects.filter(command_type="assign_handoff").exists())
        self.assertEqual(
            self.order.operations.get(sequence=3).status, ExecutionOperation.PLANNED,
        )


class DispatchNextSessionStateTests(DispatchNextApiTestBase):
    """TL review S-1: the 200 no-op only while the leg's session is running and connected."""

    ndeath_topic = "spBv1.0/ENTERPRISE_A/NDEATH/SCMS_ENTERPRISE_A_SITE_A"

    def dispatch_first_step(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        return self.leg_commands().count(), self.dcmd_count()

    def ndeath(self):
        SimulationSession.objects.filter(pk=self.session.pk).update(sparkplug_bdseq=1)
        process_sparkplug_message(
            self.ndeath_topic, encode_payload([{"name": "bdSeq", "type": "Int64", "value": 1}]),
        )
        self.session.refresh_from_db()

    def assert_not_running_409(self, counts):
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data["code"], "simulation_not_running")
        self.assertIn("not running and connected", response.data["detail"])
        self.assertNotIn("dispatched", response.data)
        self.assertEqual((self.leg_commands().count(), self.dcmd_count()), counts)

    def test_running_session_recall_is_still_the_200_noop(self):
        counts = self.dispatch_first_step()
        response = self.post(self.order.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.data["dispatched"])
        self.assertEqual(response.data["code"], "waiting_for_handoff")
        self.assertEqual((self.leg_commands().count(), self.dcmd_count()), counts)

    def test_stopped_session_recall_is_409_simulation_not_running(self):
        counts = self.dispatch_first_step()
        stop_simulation(self.session.pk)
        # The legs stay dispatched; only the hand-off is closed (pre-existing, TCL-006 resets).
        self.assertTrue(self.order.operations.filter(
            status=ExecutionOperation.DISPATCHED).exists())
        self.assertEqual(Handoff.objects.get().status, Handoff.CANCELLED)
        self.assert_not_running_409(counts)
        # The stop completes with NDEATH (desired stopped): still 409.
        self.ndeath()
        self.assertEqual(self.session.actual_state, SimulationSession.STOPPED)
        self.assert_not_running_409(counts)

    def test_disconnected_session_recall_is_409_simulation_not_running(self):
        counts = self.dispatch_first_step()
        self.ndeath()  # Godot died: desired running, node offline, awaiting NBIRTH.
        self.assertFalse(self.session.node_connected)
        self.assertEqual(self.session.actual_state, SimulationSession.STARTING)
        self.assert_not_running_409(counts)


class GodotFailureCodeTests(DispatchNextApiTestBase):
    """§3.2c: every failure = Command/Result/Reason (code) + Command/Result/Detail."""

    def test_unknown_failure_code_is_a_generic_failure_with_code_and_detail(self):
        dispatch = self.post(self.order.pk)
        self.assertEqual(dispatch.status_code, 201)
        qc_cmd = self.command_for(self.qc)
        tt_cmd = self.command_for(self.chosen_tt())
        receipt = self.result(tt_cmd, "failed", 3_000, **{
            "Command/Result/Reason": "brand_new_code_99",
            "Command/Result/Detail": "Something Godot added after rev 9.3",
        })
        self.assertEqual(receipt.status, CommandResultReceipt.APPLIED)
        self.assertEqual(receipt.raw_metrics["Command/Result/Reason"], "brand_new_code_99")
        tt_cmd.refresh_from_db()
        self.assertEqual(tt_cmd.status, EquipmentCommand.FAILED)
        operation = self.order.operations.get(sequence=2)
        self.assertEqual(operation.status, ExecutionOperation.FAILED)
        self.assertEqual(operation.failure_reason, "brand_new_code_99")
        self.assertEqual(operation.failure_detail, "Something Godot added after rev 9.3")
        self.assertEqual(operation.assignment.failure_detail, operation.failure_detail)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.FAILED)
        self.assertEqual(self.order.failure_reason, "brand_new_code_99")
        self.assertEqual(self.order.failure_detail, "Something Godot added after rev 9.3")
        failed = OutboxEvent.objects.get(payload__data__event="leg_failed")
        self.assertEqual(failed.payload["data"]["reason"], "brand_new_code_99")
        self.assertEqual(failed.payload["data"]["detail"], "Something Godot added after rev 9.3")
        # The leg failed normally: the paired QC leg is cancelled (TL review B-2).
        self.assertEqual(Handoff.objects.get().status, Handoff.CANCELLED)
        qc_cmd.refresh_from_db()
        self.assertEqual(qc_cmd.status, EquipmentCommand.CANCELLED)

    def test_known_code_without_detail_omits_detail(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd = self.command_for(self.qc)
        receipt = self.result(qc_cmd, "failed", 0, **{"Command/Result/Reason": "che_busy"})
        self.assertEqual(receipt.status, CommandResultReceipt.APPLIED)
        operation = self.order.operations.get(sequence=1)
        self.assertEqual(operation.failure_reason, "che_busy")
        self.assertEqual(operation.failure_detail, "")
        failed = OutboxEvent.objects.get(payload__data__event="leg_failed")
        self.assertEqual(failed.payload["data"]["reason"], "che_busy")
        self.assertNotIn("detail", failed.payload["data"])

    def test_null_or_blank_detail_is_empty_and_left_out_of_leg_failed(self):
        # QA M2: a Sparkplug null detail is not "None"; whitespace-only is empty.
        for order_id, value in ((51, None), (52, "   "), (53, "\t\n")):
            with self.subTest(detail=repr(value)):
                OutboxEvent.objects.all().delete()
                order = self.release(order_id)
                self.assertEqual(self.post(order.pk).status_code, 201)
                qc_cmd = self.command_for(self.qc)
                receipt = self.result(qc_cmd, "failed", 0, **{
                    "Command/Result/Reason": "che_busy", "Command/Result/Detail": value,
                })
                self.assertEqual(receipt.status, CommandResultReceipt.APPLIED)
                operation = order.operations.get(sequence=1)
                self.assertEqual(operation.failure_detail, "")
                order.refresh_from_db()
                self.assertEqual(order.failure_detail, "")
                failed = OutboxEvent.objects.get(payload__data__event="leg_failed")
                self.assertNotIn("detail", failed.payload["data"])

    def test_detail_is_stripped(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd = self.command_for(self.qc)
        self.result(qc_cmd, "failed", 0, **{
            "Command/Result/Reason": "che_busy", "Command/Result/Detail": "  crane fault \n",
        })
        self.assertEqual(self.order.operations.get(sequence=1).failure_detail, "crane fault")
        failed = OutboxEvent.objects.get(payload__data__event="leg_failed")
        self.assertEqual(failed.payload["data"]["detail"], "crane fault")

    def test_failed_without_reason_gets_the_unspecified_default(self):
        # TL TCL-028: missing, null or blank Command/Result/Reason → UNSPECIFIED.
        for order_id, extra in ((61, {}), (62, {"Command/Result/Reason": None}),
                                (63, {"Command/Result/Reason": "  "})):
            with self.subTest(reason=repr(extra.get("Command/Result/Reason", "<missing>"))):
                OutboxEvent.objects.all().delete()
                order = self.release(order_id)
                self.assertEqual(self.post(order.pk).status_code, 201)
                receipt = self.result(self.command_for(self.qc), "failed", 0, **extra)
                self.assertEqual(receipt.status, CommandResultReceipt.APPLIED)
                operation = order.operations.get(sequence=1)
                self.assertEqual(operation.failure_reason, "UNSPECIFIED")
                self.assertEqual(operation.assignment.failure_reason, "UNSPECIFIED")
                order.refresh_from_db()
                self.assertEqual(order.failure_reason, "UNSPECIFIED")
                failed = OutboxEvent.objects.get(payload__data__event="leg_failed")
                self.assertEqual(failed.payload["data"]["reason"], "UNSPECIFIED")

    def test_given_reason_is_kept_as_sent(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        self.result(self.command_for(self.qc), "failed", 0, **{
            "Command/Result/Reason": " Che_Busy-X ",
        })
        self.assertEqual(self.order.operations.get(sequence=1).failure_reason, " Che_Busy-X ")

    def test_operator_failed_event_via_api_records_detail(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd = self.command_for(self.qc)
        with self.settings(OES_API_TOKEN=TOKEN):
            response = APIClient().post(
                reverse("equipment-command-event", args=[qc_cmd.command_id]),
                {"event": "failed", "reason": "operator_abort", "detail": "crane stopped"},
                format="json", HTTP_X_OES_TOKEN=TOKEN,
            )
        self.assertEqual(response.status_code, 200, response.content)
        operation = self.order.operations.get(sequence=1)
        self.assertEqual(
            (operation.failure_reason, operation.failure_detail),
            ("operator_abort", "crane stopped"),
        )


    def operator_event(self, command, body):
        with self.settings(OES_API_TOKEN=TOKEN):
            return APIClient().post(
                reverse("equipment-command-event", args=[command.command_id]),
                body, format="json", HTTP_X_OES_TOKEN=TOKEN,
            )

    def test_completed_leg_does_not_store_detail(self):
        # TL review N-1: detail is failure text; a completed leg keeps none.
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd = self.command_for(self.qc)
        response = self.operator_event(qc_cmd, {"event": "completed", "detail": "all fine"})
        self.assertEqual(response.status_code, 200, response.content)
        operation = self.order.operations.get(sequence=1)
        self.assertEqual(operation.status, ExecutionOperation.COMPLETE)
        self.assertEqual(operation.failure_detail, "")
        self.assertEqual(operation.assignment.failure_detail, "")
        completed = OutboxEvent.objects.get(payload__data__event="leg_completed")
        self.assertNotIn("detail", completed.payload["data"])

    def test_cancelled_leg_keeps_reason_and_detail_on_the_order(self):
        # TL review N-2: same reason/detail on the order as on the operation.
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd = self.command_for(self.qc)
        response = self.operator_event(qc_cmd, {
            "event": "cancelled", "reason": "operator_abort", "detail": "berth closed",
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.CANCELLED)
        self.assertEqual(
            (self.order.failure_reason, self.order.failure_detail),
            ("operator_abort", "berth closed"),
        )
        cancelled = OutboxEvent.objects.get(payload__data__event="order_cancelled")
        self.assertEqual(cancelled.payload["data"]["detail"], "berth closed")


class FailedResultContainerMismatchTests(DispatchNextApiTestBase):
    """TL TCL-028: a failed result with another container number still fails the leg."""

    def test_failed_result_with_mismatching_number_is_applied_and_flagged(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        tt_cmd = self.command_for(self.chosen_tt())
        with self.assertLogs("execution", level="WARNING") as logs:
            receipt = self.result(tt_cmd, "failed", 3_000, **{
                "Command/Result/Reason": "container_number_invalid",
                "Command/Result/Detail": "bad number",
                "Command/Result/ContainerNumber": "TGHU0000000",
            })
        self.assertTrue(any("TGHU0000000" in line for line in logs.output))
        self.assertEqual(receipt.status, CommandResultReceipt.APPLIED)
        self.assertEqual(receipt.raw_metrics["Command/Result/ContainerNumber"], "TGHU0000000")
        operation = self.order.operations.get(sequence=2)
        self.assertEqual(operation.status, ExecutionOperation.FAILED)
        self.assertEqual(operation.failure_reason, "container_number_invalid")
        expected = f"bad number; container mismatch: reported TGHU0000000, expected {CONTAINER}"
        self.assertEqual(operation.failure_detail, expected)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.FAILED)
        self.assertEqual(self.order.failure_detail, expected)
        failed = OutboxEvent.objects.get(payload__data__event="leg_failed")
        self.assertEqual(failed.payload["data"]["detail"], expected)

    def test_failed_result_mismatch_without_detail_gets_only_the_note(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd = self.command_for(self.qc)
        self.result(qc_cmd, "failed", 0, **{
            "Command/Result/Reason": "che_busy",
            "Command/Result/ContainerNumber": "TGHU0000000",
        })
        self.assertEqual(
            self.order.operations.get(sequence=1).failure_detail,
            f"container mismatch: reported TGHU0000000, expected {CONTAINER}",
        )

    def test_cancelled_result_with_mismatching_number_is_applied_and_flagged(self):
        # TL TCL-028 (review N-4): a cancel is terminal like a failure; apply it.
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        tt_cmd = self.command_for(self.chosen_tt())
        with self.assertLogs("execution", level="WARNING") as logs:
            receipt = self.result(tt_cmd, "cancelled", 3_000, **{
                "Command/Result/Reason": "operator_abort",
                "Command/Result/Detail": "aborted",
                "Command/Result/ContainerNumber": "TGHU0000000",
            })
        self.assertTrue(any(
            "Cancelled result" in line and "TGHU0000000" in line for line in logs.output
        ))
        self.assertEqual(receipt.status, CommandResultReceipt.APPLIED)
        self.assertEqual(receipt.raw_metrics["Command/Result/ContainerNumber"], "TGHU0000000")
        tt_cmd.refresh_from_db()
        self.assertEqual(tt_cmd.status, EquipmentCommand.CANCELLED)  # not left live
        expected = f"aborted; container mismatch: reported TGHU0000000, expected {CONTAINER}"
        operation = self.order.operations.get(sequence=2)
        self.assertEqual(operation.status, ExecutionOperation.CANCELLED)
        self.assertEqual(operation.failure_detail, expected)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.CANCELLED)
        self.assertEqual(self.order.failure_detail, expected)
        cancelled = OutboxEvent.objects.get(payload__data__event="order_cancelled")
        self.assertEqual(cancelled.payload["data"]["detail"], expected)
        # The paired QC leg is cancelled with the hand-off (TL review B-2).
        self.assertEqual(Handoff.objects.get().status, Handoff.CANCELLED)

    def test_success_results_with_mismatching_number_stay_rejected(self):
        self.assertEqual(self.post(self.order.pk).status_code, 201)
        qc_cmd = self.command_for(self.qc)
        status_before = qc_cmd.status
        for event in ("acknowledged", "started", "completed", "handoff_completed"):
            receipt = self.result(qc_cmd, event, 0, **{
                "Command/Result/ContainerNumber": "TGHU0000000",
            })
            self.assertEqual(receipt.status, CommandResultReceipt.REJECTED, event)
            self.assertEqual(receipt.reason, "container_number_mismatch")
        qc_cmd.refresh_from_db()
        self.assertEqual(qc_cmd.status, status_before)
        self.assertEqual(
            self.order.operations.get(sequence=1).status, ExecutionOperation.DISPATCHED,
        )
        self.assertFalse(OutboxEvent.objects.filter(payload__data__event="leg_failed").exists())
