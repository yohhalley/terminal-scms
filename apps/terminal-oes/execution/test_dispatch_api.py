"""TCL-028: ``POST /api/orders/{id}/dispatch-next/`` and Godot failure results (§3.2c).

The endpoint dispatches the next step of a released order as TCL-002 DCMDs
(QC ``qc_discharge`` + TT ``execute_order_leg`` paired on one HandoffId, then
TT→RTG after H1 transferred). A re-call while the step is in flight is a no-op.
Results flow back through the Sparkplug result path into execution state, and
custody moves to the TT with ``container_transferred`` (MiniTOS
``transfer_container`` makes the visit On Equipment / TT).
"""

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
