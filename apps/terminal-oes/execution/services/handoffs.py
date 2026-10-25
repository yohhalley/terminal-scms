"""Hand-off record and TT no-show handling (T-006, T-007, contract rev 8 §3.3/§3.3a).

OES is the single writer of the hand-off record. Godot owns the timing: it
reports ``handoff_completed`` (both sides, Q21) and, on a D-013 no-show,
``handoff_timed_out`` (the QC, after 300 s sim time). Every function here is
idempotent; a result that cannot be applied raises ``ResultRejected`` so the
caller stores a rejected receipt instead of rolling back the DDATA.
"""

import logging

from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.utils import timezone

from execution.models import (
    DispatchAssignment,
    EquipmentCommand,
    ExecutionOperation,
    ExecutionOrder,
    Handoff,
    SimulationSession,
)
from execution.services.dispatch import (
    COMMAND_ASSIGN_HANDOFF,
    COMMAND_CANCEL,
    TERMINAL_COMMAND_STATES,
    derived_id,
    event_time,
    queue_cancel_dcmd,
)
from execution.services.events import finalize_outbox_event, queue_execution_event

logger = logging.getLogger(__name__)


class ResultRejected(ValidationError):
    """A command result that is stored with status ``rejected`` and a reason code."""

    def __init__(self, code, message="", conflict=None):
        super().__init__(message or code)
        self.reason_code = code
        # (handoff pk, text): recorded by the caller *after* the result's
        # savepoint is rolled back, so the flag survives the rejection.
        self.conflict = conflict


def _metric_int(metrics, name):
    value = metrics.get(name)
    if value in {None, ""}:
        return None
    return int(value)


def _load_handoff(command, metrics):
    data = (command.payload or {}).get("data", {})
    handoff_id = metrics.get("Command/Result/HandoffId") or data.get("handoff_id")
    if not handoff_id:
        raise ResultRejected("handoff_unknown", "The result carries no HandoffId.")
    handoff = Handoff.objects.select_for_update().filter(pk=str(handoff_id)).first()
    if handoff is None:
        raise ResultRejected("handoff_unknown", f"Unknown HandoffId {handoff_id}.")
    return handoff


def _role_for(handoff, command, metrics):
    equipment_id = command.assignment.equipment_id
    expected = {
        Handoff.GIVER: handoff.giver_equipment_id,
        Handoff.RECEIVER: handoff.receiver_equipment_id,
    }
    role = str(metrics.get("Command/Result/HandoffRole") or "").lower()
    if not role:
        role = Handoff.GIVER if equipment_id == handoff.giver_equipment_id else Handoff.RECEIVER
    if role not in expected or expected[role] != equipment_id:
        raise ResultRejected("handoff_role_mismatch", "The CHE does not hold that hand-off role.")
    return role


def _flag_conflict(handoff, text):
    """Log a conflicting report; return the marker for ``record_conflict``."""
    logger.warning("Hand-off %s conflict: %s", handoff.handoff_id, text)
    return (handoff.pk, text)


def record_conflict(conflict):
    """Persist a conflict flag outside the rejected result's savepoint."""
    handoff_pk, text = conflict
    Handoff.objects.filter(pk=handoff_pk).update(
        conflict_count=F("conflict_count") + 1,
        last_conflict=text[:200],
        updated_at=timezone.now(),
    )


def _check_container(handoff, metrics):
    visit_id = _metric_int(metrics, "Command/Result/ContainerVisitId")
    number = str(metrics.get("Command/Result/ContainerNumber") or "")
    if visit_id is not None and handoff.container_visit_id is not None:
        if visit_id != handoff.container_visit_id:
            raise ResultRejected("container_visit_mismatch", conflict=_flag_conflict(
                handoff, f"ContainerVisitId {visit_id} != {handoff.container_visit_id}",
            ))
    if number and handoff.container_number and number != handoff.container_number:
        raise ResultRejected("container_number_mismatch", conflict=_flag_conflict(
            handoff, f"ContainerNumber {number} != {handoff.container_number}",
        ))


def apply_handoff_completed(command, metrics, *, result_id, event_ms):
    """Apply one side's ``handoff_completed``. Returns ``True`` when it transferred custody."""
    handoff = _load_handoff(command, metrics)
    role = _role_for(handoff, command, metrics)
    if handoff.status != Handoff.PENDING and handoff.status != Handoff.TRANSFERRED:
        logger.warning(
            "Late handoff_completed for %s hand-off %s (role %s) rejected.",
            handoff.status, handoff.handoff_id, role,
        )
        raise ResultRejected("handoff_not_pending")
    _check_container(handoff, metrics)
    field = "giver_result_id" if role == Handoff.GIVER else "receiver_result_id"
    previous = getattr(handoff, field)
    if previous is not None and str(previous) != str(result_id):
        raise ResultRejected("handoff_conflict", conflict=_flag_conflict(
            handoff, f"second {role} report {result_id} (first {previous})",
        ))
    setattr(handoff, field, result_id)
    if handoff.status == Handoff.TRANSFERRED:
        if event_ms is not None and handoff.transferred_event_ms not in {None, int(event_ms)}:
            record_conflict(_flag_conflict(
                handoff, f"{role} Event/TimeMs {event_ms} != {handoff.transferred_event_ms}",
            ))
        handoff.save(update_fields=[field, "updated_at"])
        return False

    handoff.status = Handoff.TRANSFERRED
    handoff.transferred_event_ms = int(event_ms) if event_ms is not None else None
    handoff.save(update_fields=[field, "status", "transferred_event_ms", "updated_at"])
    order = handoff.order
    outgoing = queue_execution_event(
        order,
        "container_transferred",
        operation=handoff.receiver_operation,
        equipment=handoff.receiver_equipment,
        extra={
            "handoff_id": str(handoff.handoff_id),
            "from_che_id": handoff.giver_equipment.tos_che_id,
            "to_che_id": handoff.receiver_equipment.tos_che_id,
            "giver_leg_id": handoff.giver_operation.tos_leg_id,
            "receiver_leg_id": handoff.receiver_operation.tos_leg_id,
            "container_visit_id": handoff.container_visit_id,
            "container_number": handoff.container_number or None,
            "transfer_position_id": handoff.transfer_position_id,
        },
        occurred_at=event_time(event_ms),
        event_id=derived_id("container_transferred", handoff.handoff_id),
    )
    finalize_outbox_event(outgoing)
    return True


def cancel_command(command, *, reason, anchor_sim_ms=None):
    """OES-initiated cancel: mark the command cancelled and queue a cancel DCMD (T-007)."""
    if command.status in TERMINAL_COMMAND_STATES:
        return None
    command.status = EquipmentCommand.CANCELLED
    command.completed_at = timezone.now()
    command.save(update_fields=["status", "completed_at"])
    return queue_cancel_dcmd(command, reason=reason, anchor_sim_ms=anchor_sim_ms)


def _cancel_operation(operation, *, reason, event_ms, anchor_sim_ms):
    now = timezone.now()
    if operation.status in {ExecutionOperation.COMPLETE, ExecutionOperation.CANCELLED}:
        return False
    assignment = DispatchAssignment.objects.filter(operation=operation).first()
    if assignment is not None:
        for command in assignment.commands.exclude(command_type=COMMAND_CANCEL).order_by(
            "created_at", "command_id",
        ):
            if command.command_type == COMMAND_ASSIGN_HANDOFF:
                # Godot holds one active command per CHE: cancelling the leg
                # command clears the CHE; the H2 add-on just closes in OES.
                if command.status not in TERMINAL_COMMAND_STATES:
                    command.status = EquipmentCommand.CANCELLED
                    command.completed_at = now
                    command.save(update_fields=["status", "completed_at"])
                continue
            cancel_command(command, reason=reason, anchor_sim_ms=anchor_sim_ms)
        if assignment.status not in {DispatchAssignment.COMPLETE, DispatchAssignment.FAILED}:
            assignment.status = DispatchAssignment.CANCELLED
            assignment.completed_at = now
            assignment.failure_reason = reason
            assignment.save(update_fields=["status", "completed_at", "failure_reason"])
        equipment = assignment.equipment
        equipment.working = False
        equipment.is_dispatched = False
        equipment.is_available = equipment.powered_on and not equipment.is_in_maintenance
        if event_ms is not None:
            equipment.idle_since_event_ms = int(event_ms)
        equipment.save(update_fields=[
            "working", "is_dispatched", "is_available", "idle_since_event_ms",
        ])
    if operation.status not in {ExecutionOperation.COMPLETE, ExecutionOperation.CANCELLED}:
        operation.status = ExecutionOperation.CANCELLED
        operation.actual_end = now
        operation.failure_reason = reason
        operation.save(update_fields=["status", "actual_end", "failure_reason"])
        return True
    return False


def apply_handoff_timed_out(command, metrics, *, event_ms, sim_ms=None):
    """D-013/T-007: Godot reports the TT no-show; raise the hand-off exception once.

    Returns ``True`` when the exception was raised, ``False`` for a repeat.
    """
    handoff = _load_handoff(command, metrics)
    role = _role_for(handoff, command, metrics)
    if role != Handoff.GIVER:
        raise ResultRejected("handoff_role_mismatch", "Only the giver reports a no-show.")
    if handoff.status == Handoff.EXCEPTION:
        return False
    if handoff.status != Handoff.PENDING:
        logger.warning(
            "handoff_timed_out for %s hand-off %s rejected.", handoff.status, handoff.handoff_id,
        )
        raise ResultRejected("handoff_not_pending")
    _check_container(handoff, metrics)
    reason = Handoff.NO_TT_TIMEOUT
    handoff.status = Handoff.EXCEPTION
    handoff.exception_reason = reason
    handoff.exception_event_ms = int(event_ms) if event_ms is not None else None
    handoff.save(update_fields=[
        "status", "exception_reason", "exception_event_ms", "updated_at",
    ])

    order = ExecutionOrder.objects.select_for_update().get(pk=handoff.order_id)
    cancelled_legs = []
    for operation in order.operations.select_for_update().order_by("sequence"):
        if _cancel_operation(
            operation, reason=reason, event_ms=event_ms, anchor_sim_ms=sim_ms,
        ):
            cancelled_legs.append(operation.tos_leg_id)
    order.status = ExecutionOrder.CANCELLED
    order.failure_reason = reason
    order.completed_at = timezone.now()
    order.save(update_fields=["status", "failure_reason", "completed_at", "updated_at"])
    last_leg = order.operations.order_by("-sequence").first()
    outgoing = queue_execution_event(
        order,
        "handoff_exception",
        operation=handoff.giver_operation,
        equipment=handoff.giver_equipment,
        extra={
            "handoff_id": str(handoff.handoff_id),
            "reason": reason,
            "from_che_id": handoff.giver_equipment.tos_che_id,
            "to_che_id": handoff.receiver_equipment.tos_che_id,
            "giver_leg_id": handoff.giver_operation.tos_leg_id,
            "receiver_leg_id": handoff.receiver_operation.tos_leg_id,
            "cancelled_leg_ids": cancelled_legs,
            "container_visit_id": handoff.container_visit_id,
            "container_number": handoff.container_number or None,
            "destination_position_id": (
                last_leg.destination_inventory_position_id if last_leg else None
            ),
        },
        occurred_at=event_time(event_ms),
        event_id=derived_id("handoff_exception", handoff.handoff_id),
    )
    finalize_outbox_event(outgoing)
    return True


def abort_handoffs_for_operation(operation, *, reason, event_ms=None, sim_ms=None):
    """TL review B-2: a leg of a pending hand-off ended without a transfer.

    The hand-off goes ``cancelled`` and the counterpart leg is cancelled with a
    cancel DCMD, so neither CHE stays locked out or keeps a live command.
    Returns the cancelled hand-offs.
    """
    closed = []
    pending = Handoff.objects.select_for_update().filter(
        Q(giver_operation=operation) | Q(receiver_operation=operation),
        status=Handoff.PENDING,
    ).select_related("giver_operation", "receiver_operation")
    for handoff in pending.order_by("created_at", "handoff_id"):
        handoff.status = Handoff.CANCELLED
        handoff.exception_reason = reason[:40]
        handoff.exception_event_ms = int(event_ms) if event_ms is not None else None
        handoff.save(update_fields=[
            "status", "exception_reason", "exception_event_ms", "updated_at",
        ])
        counterpart = (
            handoff.receiver_operation
            if handoff.giver_operation_id == operation.pk
            else handoff.giver_operation
        )
        _cancel_operation(counterpart, reason=reason, event_ms=event_ms, anchor_sim_ms=sim_ms)
        logger.warning(
            "Hand-off %s cancelled (%s); counterpart leg %s cancelled.",
            handoff.handoff_id, reason, counterpart.tos_leg_id,
        )
        closed.append(handoff)
    return closed


def close_session_handoffs(sessions, *, reason):
    """TL review B-2: a stopped, failed or abandoned session leaves no pending hand-off.

    No DCMD is sent: the session's simulator is gone or going. Returns the count.
    """
    if isinstance(sessions, SimulationSession):
        sessions = [sessions]
    return Handoff.objects.filter(
        simulation_session__in=list(sessions), status=Handoff.PENDING,
    ).update(status=Handoff.CANCELLED, exception_reason=reason[:40], updated_at=timezone.now())
