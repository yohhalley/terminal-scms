import logging
import random
import uuid
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from execution.models import (
    DispatchAssignment,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    Handoff,
    OutboxEvent,
    SimulationSession,
)
from execution.services.events import finalize_outbox_event, queue_execution_event
from execution.sparkplug import encode_payload

logger = logging.getLogger(__name__)

# Fixed namespace for OES-derived ids (HandoffId, business event ids). Changing it
# changes every derived id, so it is part of the D-016 determinism contract.
OES_ID_NAMESPACE = uuid.UUID("6f1c1e52-3b0a-4f43-9d4e-0c6b7e0a0003")

COMMAND_EXECUTE_ORDER_LEG = "execute_order_leg"
COMMAND_QC_DISCHARGE = "qc_discharge"
COMMAND_QC_LOAD = "qc_load"
COMMAND_ASSIGN_HANDOFF = "assign_handoff"
COMMAND_CANCEL = "cancel"  # TCL-014a HeadlessSimulator.gd COMMAND_CANCEL
QC_EQUIPMENT_TYPE_CODES = {"STS", "QC"}
TERMINAL_COMMAND_STATES = {
    EquipmentCommand.COMPLETE,
    EquipmentCommand.FAILED,
    EquipmentCommand.CANCELLED,
}
DONE_OPERATION_STATES = {ExecutionOperation.COMPLETE, ExecutionOperation.CANCELLED}
ACTIVE_OPERATION_STATES = {ExecutionOperation.DISPATCHED, ExecutionOperation.WORKING}


def derived_id(*parts):
    """Deterministic UUID for OES-derived identities (D-016)."""
    return uuid.uuid5(OES_ID_NAMESPACE, ":".join(str(part) for part in parts))


def event_time(event_ms):
    """Business-event time from ``Event/TimeMs`` (contract §2.1), else None."""
    if event_ms is None:
        return None
    return datetime.fromtimestamp(int(event_ms) / 1000, tz=datetime_timezone.utc)


def is_handoff_boundary(giver_operation):
    """T-006: a leg that ends without a yard position hands its container to the next CHE."""
    return giver_operation.destination_inventory_position_id is None


def _is_qc(equipment):
    return (equipment.equipment_type_code or "").upper() in QC_EQUIPMENT_TYPE_CODES


def _pending_handoff_for(equipment, session):
    """A CHE is busy while it has a pending hand-off *in this session* (TL review B-2)."""
    return Handoff.objects.filter(
        Q(giver_equipment=equipment) | Q(receiver_equipment=equipment),
        status=Handoff.PENDING,
        simulation_session=session,
    ).exists()


def _plan_dispatch(order):
    """Return ``(giver_or_single, receiver, chained)`` for the next dispatch step.

    * single: one leg, no hand-off at its end (``receiver`` is None).
    * pair: both legs are planned and the first ends in a hand-off (B1, T-006).
    * chained: the giver is already active, its own incoming hand-off has
      transferred, and its leg ends in a hand-off (Q18: TT→RTG after QC→TT).
    """
    operations = list(order.operations.select_for_update().order_by("sequence"))
    for index, operation in enumerate(operations):
        if operation.status in DONE_OPERATION_STATES:
            continue
        if operation.status in ACTIVE_OPERATION_STATES:
            continue
        if operation.status != ExecutionOperation.PLANNED:
            raise ValidationError("The next order operation is already assigned or blocked.")
        previous = operations[index - 1] if index else None
        following = operations[index + 1] if index + 1 < len(operations) else None
        if previous is None or previous.status == ExecutionOperation.COMPLETE:
            if any(earlier.status not in DONE_OPERATION_STATES for earlier in operations[:index]):
                raise ValidationError("An earlier order operation is not complete.")
            if following is not None and is_handoff_boundary(operation):
                if following.status != ExecutionOperation.PLANNED:
                    raise ValidationError("The hand-off receiver leg is not plannable.")
                return operation, following, False
            return operation, None, False
        if previous.status in ACTIVE_OPERATION_STATES and is_handoff_boundary(previous):
            incoming = previous.incoming_handoffs.first()
            if incoming is not None and incoming.status != Handoff.TRANSFERRED:
                raise ValidationError(
                    "Q18: the next hand-off waits until the previous hand-off has transferred."
                )
            if previous.outgoing_handoffs.exists():
                raise ValidationError("The order's next hand-off is already dispatched.")
            return previous, operation, True
        raise ValidationError("An earlier order operation is not complete.")
    raise ValidationError("The order has no remaining operation to dispatch.")


def _eligible_equipment(operation, session):
    stale_seconds = int(getattr(settings, "EQUIPMENT_STALE_SECONDS", 60))
    query = EquipmentState.objects.select_for_update().filter(
        site=operation.order.site,
        simulation_session=session,
        powered_on=True,
        is_available=True,
        working=False,
        is_dispatched=False,
        is_in_maintenance=False,
        device_connected=True,
        data_valid=True,
        last_seen_at__gte=timezone.now() - timedelta(seconds=stale_seconds),
    )
    if operation.required_equipment_type_id:
        query = query.filter(equipment_type_id=operation.required_equipment_type_id)
    return list(query)


def session_event_ms(session, event_ms=None):
    """Event time on the simulator clock (TL review S-3 / A-6).

    ``event_ms`` when the result carried Event/TimeMs; otherwise the session's
    current sim time (Sim/StartEpochMs + latest Sim/TimeMs), so idle-since never
    mixes wall time with sim time. Wall time only when there is no simulator
    clock at all (no session, or a session that never reported its epoch).
    """
    if event_ms is not None:
        return int(event_ms)
    if session is not None and session.start_epoch_ms is not None:
        return int(session.start_epoch_ms) + int(session.last_sim_time_ms or 0)
    return int(timezone.now().timestamp() * 1000)


def select_equipment(candidates, *, seed, order_id):
    """D-014/T-007: the CHE idle longest on Event/TimeMs; ties by a PRNG seeded from Sim/Seed + OrderId.

    Candidates are sorted by ``tos_che_id`` before the draw so the choice never
    depends on database row order (D-016). A CHE with no recorded idle time
    counts as idle since the start of the run (0).
    """
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda item: item.tos_che_id)
    earliest = min(item.idle_since_event_ms or 0 for item in ordered)
    tied = [item for item in ordered if (item.idle_since_event_ms or 0) == earliest]
    if len(tied) == 1:
        return tied[0]
    if seed is None:
        # TL review S-6: never draw from an unseeded (silently 0) PRNG; a
        # session without Sim/Seed cannot break a tie reproducibly (D-016).
        raise ValidationError(
            "D-016: Sim/Seed is missing for this session; cannot break the CHE tie.",
        )
    rng = random.Random(f"{seed}:{order_id}")
    return tied[rng.randrange(len(tied))]


def _explicit_equipment(equipment_id, order, operation, session):
    equipment = EquipmentState.objects.select_for_update().filter(
        pk=equipment_id,
        site=order.site,
        powered_on=True,
        is_available=True,
        working=False,
        is_dispatched=False,
        is_in_maintenance=False,
        device_connected=True,
        data_valid=True,
        simulation_session=session,
    ).first()
    if equipment and operation.required_equipment_type_id:
        if equipment.equipment_type_id != operation.required_equipment_type_id:
            equipment = None
    return equipment


def _choose(operation, session, order, *, exclude=()):
    candidates = [
        item for item in _eligible_equipment(operation, session)
        if item.pk not in exclude and not _pending_handoff_for(item, session)
    ]
    equipment = select_equipment(candidates, seed=session.seed, order_id=order.tos_order_id)
    if equipment is None:
        raise ValidationError("No fresh, available CHE satisfies this operation.")
    return equipment


def _command_topic(session, equipment):
    device_id = equipment.sparkplug_device_id
    if not device_id:
        # R4.1/Q11: no silent CHE_<id> fallback; the device id comes from DBIRTH.
        raise ValidationError("The selected CHE has no Sparkplug device id (no DBIRTH yet).")
    return "spBv1.0/{}/DCMD/{}/{}".format(
        session.sparkplug_group_id,
        session.sparkplug_node_id,
        device_id,
    )


def command_ttl_seconds(session):
    """Sim/Timing/CommandTtlS from NBIRTH, else the OES default (600 s sim)."""
    if session is not None and session.command_ttl_s:
        return int(session.command_ttl_s)
    return int(getattr(settings, "EQUIPMENT_COMMAND_TTL_SIM_SECONDS", 600))


def command_expiry_sim_ms(session, anchor_sim_ms=None):
    """TCL-028: ``Command/ExpiresAtMs`` on the Sim/TimeMs clock.

    ``anchor_sim_ms`` is the Sim/TimeMs of the result that caused the command
    (H1 transfer for H2, the time-out for cancels), so the value is identical
    on a rerun (D-016). Operator-triggered dispatches fall back to the highest
    Sim/TimeMs seen in the session.
    """
    ttl_ms = command_ttl_seconds(session) * 1000
    anchor = session.last_sim_time_ms if anchor_sim_ms is None else int(anchor_sim_ms)
    return int(anchor or 0) + ttl_ms


def _base_metrics(command, assignment, session, order, equipment, expires_at_sim_ms):
    return [
        {"name": "Command/Id", "type": "String", "value": str(command.command_id)},
        {"name": "Command/Type", "type": "String", "value": command.command_type},
        {"name": "Command/AssignmentId", "type": "String", "value": str(assignment.pk)},
        {"name": "Command/SessionId", "type": "String", "value": str(session.pk)},
        {"name": "Command/SiteId", "type": "String", "value": str(order.site.source_id)},
        {
            "name": "Command/ConfigurationRevision",
            "type": "Int64",
            "value": session.configuration_revision,
        },
        {"name": "Command/ExpiresAtMs", "type": "Int64", "value": expires_at_sim_ms},
        {"name": "Command/CheId", "type": "Int64", "value": equipment.tos_che_id},
        {"name": "Command/OrderId", "type": "Int64", "value": order.tos_order_id},
    ]


def _handoff_data(handoff, role, counterpart):
    if handoff is None:
        return {}
    return {
        "handoff_id": str(handoff.handoff_id),
        "handoff_role": role,
        "counterpart_che_id": counterpart.tos_che_id,
        "transfer_position_id": handoff.transfer_position_id,
    }


def _handoff_metrics(data):
    if not data:
        return []
    metrics = [
        {"name": "Command/HandoffId", "type": "String", "value": data["handoff_id"]},
        {"name": "Command/HandoffRole", "type": "String", "value": data["handoff_role"]},
        {"name": "Command/CounterpartCheId", "type": "Int64", "value": data["counterpart_che_id"]},
    ]
    if data.get("transfer_position_id") is not None:
        metrics.append({
            "name": "Command/TransferPositionId",
            "type": "Int64",
            "value": data["transfer_position_id"],
        })
    return metrics


def _issue_leg_command(
    session, order, operation, equipment, assignment, *, handoffs=(), anchor_sim_ms=None,
):
    """Create and queue the leg DCMD. ``handoffs`` is ``[(handoff, role, counterpart)]``."""
    topic = _command_topic(session, equipment)
    expires_at = command_expiry_sim_ms(session, anchor_sim_ms)
    roles = {role for _handoff, role, _counterpart in handoffs}
    if _is_qc(equipment) and Handoff.GIVER in roles:
        command_type = COMMAND_QC_DISCHARGE
    elif _is_qc(equipment) and Handoff.RECEIVER in roles:
        command_type = COMMAND_QC_LOAD
    else:
        command_type = COMMAND_EXECUTE_ORDER_LEG
    command = EquipmentCommand.objects.create(
        assignment=assignment,
        simulation_session=session,
        command_type=command_type,
        topic=topic,
        expires_at_sim_ms=expires_at,
    )
    # A leg carries at most one hand-off per DCMD (Q18). A receiver that later
    # gives (TT in H2) gets it through ``assign_handoff``.
    handoff_data = _handoff_data(*handoffs[0]) if handoffs else {}
    data = {
        "command": command.command_type,
        "order_id": order.tos_order_id,
        "leg_id": operation.tos_leg_id,
        "che_id": equipment.tos_che_id,
        "container_visit_id": order.container_visit_id,
        "container_number": order.container_number or None,
        "source_position_id": operation.source_position_id,
        "destination_position_id": operation.destination_position_id,
        "source_inventory_position_id": operation.source_inventory_position_id,
        "destination_inventory_position_id": operation.destination_inventory_position_id,
        "destination_position_code": (
            (operation.source_payload or {}).get("destination_position_code") or None
        ),
        "source_latitude": operation.source_latitude,
        "source_longitude": operation.source_longitude,
        "destination_latitude": operation.destination_latitude,
        "destination_longitude": operation.destination_longitude,
        "movement_mode": operation.required_movement_mode,
        **handoff_data,
    }
    command.payload = {
        "schema": "scms.equipment.command.v1",
        "command_id": str(command.command_id),
        "site": {"id": str(order.site.source_id), "code": order.site.code},
        "session": {
            "id": str(session.pk),
            "configuration_revision": session.configuration_revision,
            "expires_at_sim_ms": expires_at,
        },
        "data": data,
    }
    command.save(update_fields=["payload"])
    metrics = _base_metrics(command, assignment, session, order, equipment, expires_at)
    metrics.extend([
        {"name": "Command/LegId", "type": "Int64", "value": operation.tos_leg_id},
        {
            "name": "Command/MovementMode",
            "type": "String",
            "value": operation.required_movement_mode,
        },
    ])
    optional_metrics = [
        ("Command/ContainerVisitId", "Int64", data.get("container_visit_id")),
        ("Command/ContainerNumber", "String", data.get("container_number")),
        ("Command/SourcePositionId", "Int64", data.get("source_position_id")),
        # Destination names as read by Godot TCL-014b (contract rev 9.3).
        ("Command/DestinationPositionId", "Int64", data.get("destination_position_id")),
        ("Command/DestinationPositionCode", "String", data.get("destination_position_code")),
        (
            "Command/SourceInventoryPositionId",
            "Int64",
            data.get("source_inventory_position_id"),
        ),
        (
            "Command/DestinationInventoryPositionId",
            "Int64",
            data.get("destination_inventory_position_id"),
        ),
        ("Command/SourceLatitude", "Double", data.get("source_latitude")),
        ("Command/SourceLongitude", "Double", data.get("source_longitude")),
        ("Command/DestinationLatitude", "Double", data.get("destination_latitude")),
        ("Command/DestinationLongitude", "Double", data.get("destination_longitude")),
    ]
    metrics.extend(
        {"name": name, "type": data_type, "value": value}
        for name, data_type, value in optional_metrics
        if value is not None
    )
    metrics.extend(_handoff_metrics(handoff_data))
    _queue_command(command, metrics)
    return command


def _queue_command(command, metrics):
    OutboxEvent.objects.create(
        event_id=command.command_id,
        topic=command.topic,
        schema="scms.equipment.command.v1",
        payload=command.payload,
        payload_bytes=encode_payload(metrics, sequence=0),
        qos=1,
    )


def _issue_assign_handoff(
    session, order, giver_operation, handoff, receiver_equipment, *, anchor_sim_ms=None,
):
    """Give an already active CHE (TT) its next hand-off as giver (Q18, H2)."""
    assignment = giver_operation.assignment
    equipment = assignment.equipment
    expires_at = command_expiry_sim_ms(session, anchor_sim_ms)
    command = EquipmentCommand.objects.create(
        assignment=assignment,
        simulation_session=session,
        command_type=COMMAND_ASSIGN_HANDOFF,
        topic=_command_topic(session, equipment),
        expires_at_sim_ms=expires_at,
    )
    handoff_data = _handoff_data(handoff, Handoff.GIVER, receiver_equipment)
    command.payload = {
        "schema": "scms.equipment.command.v1",
        "command_id": str(command.command_id),
        "site": {"id": str(order.site.source_id), "code": order.site.code},
        "session": {
            "id": str(session.pk),
            "configuration_revision": session.configuration_revision,
            "expires_at_sim_ms": expires_at,
        },
        "data": {
            "command": command.command_type,
            "order_id": order.tos_order_id,
            "leg_id": giver_operation.tos_leg_id,
            "che_id": equipment.tos_che_id,
            "container_visit_id": order.container_visit_id,
            "container_number": order.container_number or None,
            **handoff_data,
        },
    }
    command.save(update_fields=["payload"])
    metrics = _base_metrics(command, assignment, session, order, equipment, expires_at)
    metrics.append({"name": "Command/LegId", "type": "Int64", "value": giver_operation.tos_leg_id})
    if order.container_visit_id is not None:
        metrics.append({
            "name": "Command/ContainerVisitId", "type": "Int64",
            "value": order.container_visit_id,
        })
    if order.container_number:
        metrics.append({
            "name": "Command/ContainerNumber", "type": "String",
            "value": order.container_number,
        })
    metrics.extend(_handoff_metrics(handoff_data))
    _queue_command(command, metrics)
    return command


def queue_cancel_dcmd(command, *, reason, anchor_sim_ms=None):
    """Queue the "normal cancel command" (T-007, TCL-014a) for ``command``'s CHE.

    Format agreed with Godot TCL-014a (HeadlessSimulator.gd:11, :314-330, :383-423):
    ``Command/Type = cancel``, ``Command/CancelCommandId`` = the target, and the
    usual envelope incl. ``AssignmentId`` and ``ExpiresAtMs``. The cancel id is
    derived from the target, so a retried cancel is the same message and Godot
    replays the same ``cancelled`` result instead of cancelling a newer command.
    """
    assignment = command.assignment
    session = command.simulation_session
    order = assignment.operation.order
    equipment = assignment.equipment
    cancel_id = derived_id("cancel", command.command_id)
    existing = EquipmentCommand.objects.filter(pk=cancel_id).first()
    if existing is not None:
        return existing
    expires_at = command_expiry_sim_ms(session, anchor_sim_ms)
    cancel = EquipmentCommand.objects.create(
        command_id=cancel_id,
        assignment=assignment,
        simulation_session=session,
        command_type=COMMAND_CANCEL,
        topic=command.topic,
        expires_at_sim_ms=expires_at,
    )
    cancel.payload = {
        "schema": "scms.equipment.command.v1",
        "command_id": str(cancel.command_id),
        "site": {"id": str(order.site.source_id), "code": order.site.code},
        "session": {
            "id": str(session.pk),
            "configuration_revision": session.configuration_revision,
            "expires_at_sim_ms": expires_at,
        },
        "data": {
            "command": COMMAND_CANCEL,
            "cancel_command_id": str(command.command_id),
            "reason": reason,
            "che_id": equipment.tos_che_id,
            "order_id": order.tos_order_id,
        },
    }
    cancel.save(update_fields=["payload"])
    metrics = _base_metrics(cancel, assignment, session, order, equipment, expires_at)
    metrics.extend([
        {"name": "Command/CancelCommandId", "type": "String", "value": str(command.command_id)},
        {"name": "Command/Reason", "type": "String", "value": reason or "cancelled"},
    ])
    _queue_command(cancel, metrics)
    return cancel


def _create_handoff(session, order, giver_operation, receiver_operation, giver, receiver):
    handoff_id = derived_id(
        "handoff", session.pk, order.tos_order_id,
        giver_operation.tos_leg_id, receiver_operation.tos_leg_id,
    )
    return Handoff.objects.create(
        handoff_id=handoff_id,
        simulation_session=session,
        order=order,
        giver_operation=giver_operation,
        receiver_operation=receiver_operation,
        giver_equipment=giver,
        receiver_equipment=receiver,
        container_visit_id=order.container_visit_id,
        container_number=order.container_number,
        transfer_position_id=(
            giver_operation.destination_position_id or receiver_operation.source_position_id
        ),
    )


def _transfer_sim_ms(session, giver_operation):
    incoming = giver_operation.incoming_handoffs.first()
    if incoming is None or incoming.transferred_event_ms is None:
        return None
    if session.start_epoch_ms is None:
        return None
    return incoming.transferred_event_ms - session.start_epoch_ms


def _mark_dispatched(order, operation, equipment):
    operation.status = ExecutionOperation.DISPATCHED
    operation.save(update_fields=["status"])
    equipment.is_dispatched = True
    equipment.is_available = False
    equipment.save(update_fields=["is_dispatched", "is_available"])
    event = queue_execution_event(
        order, "leg_dispatched", operation=operation, equipment=equipment,
    )
    finalize_outbox_event(event)


def _active_session(order):
    session = SimulationSession.objects.select_for_update().filter(
        site=order.site,
        desired_state=SimulationSession.RUNNING_DESIRED,
        actual_state=SimulationSession.RUNNING,
        node_connected=True,
    ).first()
    if session is None:
        raise ValidationError("The Site has no connected, running Sparkplug simulation.")
    return session


@transaction.atomic
def dispatch_next_operation(order_id, *, equipment_id=None):
    """Dispatch the next step of an order and return the first new assignment.

    T-006/B1: a leg that ends in a hand-off is dispatched together with the
    receiving leg; both DCMDs carry the same HandoffId and each other's CHE.
    Q18: the TT→RTG hand-off is dispatched only after QC→TT has transferred.
    """
    order = ExecutionOrder.objects.select_for_update().get(pk=order_id)
    if order.status not in {ExecutionOrder.RELEASED, ExecutionOrder.IN_PROGRESS}:
        raise ValidationError("Only released or active orders can be dispatched.")
    first, receiver_operation, chained = _plan_dispatch(order)
    session = _active_session(order)

    if chained:
        giver = first.assignment.equipment
        if _pending_handoff_for(giver, session):
            raise ValidationError("Q18: the CHE already has an active hand-off.")
        receiver = (
            _explicit_equipment(equipment_id, order, receiver_operation, session)
            if equipment_id is not None
            else _choose(receiver_operation, session, order, exclude={giver.pk})
        )
        if receiver is None:
            raise ValidationError("No fresh, available CHE satisfies this operation.")
        handoff = _create_handoff(session, order, first, receiver_operation, giver, receiver)
        anchor = _transfer_sim_ms(session, first)
        _issue_assign_handoff(session, order, first, handoff, receiver, anchor_sim_ms=anchor)
        assignment = DispatchAssignment.objects.create(
            operation=receiver_operation, equipment=receiver,
        )
        _issue_leg_command(
            session, order, receiver_operation, receiver, assignment,
            handoffs=[(handoff, Handoff.RECEIVER, giver)], anchor_sim_ms=anchor,
        )
        _mark_dispatched(order, receiver_operation, receiver)
        return assignment

    if equipment_id is None:
        equipment = _choose(first, session, order)
    else:
        equipment = _explicit_equipment(equipment_id, order, first, session)
    if equipment is None:
        raise ValidationError("No fresh, available CHE satisfies this operation.")
    if equipment.simulation_session_id != session.pk:
        raise ValidationError("The selected CHE does not belong to the active simulation session.")

    receiver = None
    handoff = None
    if receiver_operation is not None:
        receiver = _choose(receiver_operation, session, order, exclude={equipment.pk})
        handoff = _create_handoff(session, order, first, receiver_operation, equipment, receiver)

    assignment = DispatchAssignment.objects.create(operation=first, equipment=equipment)
    _issue_leg_command(
        session, order, first, equipment, assignment,
        handoffs=[(handoff, Handoff.GIVER, receiver)] if handoff else (),
    )
    if order.status == ExecutionOrder.RELEASED:
        order.status = ExecutionOrder.IN_PROGRESS
        order.started_at = timezone.now()
        order.save(update_fields=["status", "started_at", "updated_at"])
    _mark_dispatched(order, first, equipment)
    if handoff is not None:
        receiver_assignment = DispatchAssignment.objects.create(
            operation=receiver_operation, equipment=receiver,
        )
        _issue_leg_command(
            session, order, receiver_operation, receiver, receiver_assignment,
            handoffs=[(handoff, Handoff.RECEIVER, equipment)],
        )
        _mark_dispatched(order, receiver_operation, receiver)
    return assignment


@transaction.atomic
def apply_command_event(
    command_id, event, *, position_id=None, reason="", event_ms=None, source=None,
    notify_che=False,
):
    """Apply one command result or operator event.

    ``notify_che`` is set by the OES API: an operator ``cancelled`` must also
    tell the CHE (B10/TCL-028), so a cancel DCMD is queued. Results that come
    from the CHE itself never echo a DCMD back.
    """
    command = EquipmentCommand.objects.select_for_update().select_related(
        "assignment__operation__order", "assignment__equipment",
    ).get(pk=command_id)
    assignment = command.assignment
    operation = assignment.operation
    order = operation.order
    equipment = assignment.equipment
    now = timezone.now()
    occurred_at = event_time(event_ms)

    terminal_statuses = {
        "completed": EquipmentCommand.COMPLETE,
        "failed": EquipmentCommand.FAILED,
        "cancelled": EquipmentCommand.CANCELLED,
    }
    if event in terminal_statuses and command.status == terminal_statuses[event]:
        return command
    if command.status in {
        EquipmentCommand.COMPLETE,
        EquipmentCommand.FAILED,
        EquipmentCommand.CANCELLED,
    }:
        raise ValidationError("A terminal command cannot transition to another result.")

    if event == "acknowledged":
        if command.status == EquipmentCommand.ACKNOWLEDGED:
            return command
        command.status = EquipmentCommand.ACKNOWLEDGED
        command.acknowledged_at = now
        command.save(update_fields=["status", "acknowledged_at"])
        assignment.status = DispatchAssignment.ACCEPTED
        assignment.accepted_at = now
        assignment.save(update_fields=["status", "accepted_at"])
        return command
    if event == "started":
        command.status = EquipmentCommand.ACKNOWLEDGED
        command.acknowledged_at = command.acknowledged_at or now
        command.save(update_fields=["status", "acknowledged_at"])
        assignment.status = DispatchAssignment.EXECUTING
        assignment.started_at = assignment.started_at or now
        assignment.save(update_fields=["status", "started_at"])
        operation.status = ExecutionOperation.WORKING
        operation.actual_start = operation.actual_start or now
        operation.save(update_fields=["status", "actual_start"])
        equipment.working = True
        equipment.save(update_fields=["working"])
        outgoing = queue_execution_event(
            order, "leg_started", operation=operation, equipment=equipment,
            occurred_at=occurred_at,
        )
        finalize_outbox_event(outgoing)
        return command
    if event in {"container_picked_up", "container_dropped"}:
        resolved_position_id = position_id
        if event == "container_dropped" and resolved_position_id is None:
            resolved_position_id = operation.destination_inventory_position_id
        if event == "container_dropped" and resolved_position_id is None:
            raise ValidationError("container_dropped requires an inventory position.")
        if event == "container_dropped" and operation.outgoing_handoffs.exists():
            # Q8-R5: a giver hands over with handoff_completed, never by dropping.
            raise ValidationError("container_dropped is not valid for a hand-off giver.")
        extra = {"position_id": resolved_position_id} if resolved_position_id else {}
        if event == "container_picked_up":
            if source is None and command.command_type == COMMAND_QC_DISCHARGE:
                source = "VESSEL"  # D-012/T-007: the container appears on the QC.
            if source:
                extra["source"] = str(source).upper()
            if order.container_visit_id is not None:
                extra["container_visit_id"] = order.container_visit_id
            if order.container_number:
                extra["container_number"] = order.container_number
        outgoing = queue_execution_event(
            order, event, operation=operation, equipment=equipment, extra=extra or None,
            occurred_at=occurred_at,
        )
        finalize_outbox_event(outgoing)
        return command
    if event not in {"completed", "failed", "cancelled"}:
        raise ValidationError(f"Unsupported command event: {event}.")

    failed = event == "failed"
    cancelled = event == "cancelled"
    if cancelled and notify_che and command.simulation_session_id:
        queue_cancel_dcmd(command, reason=reason)
    command.status = (
        EquipmentCommand.FAILED if failed else
        EquipmentCommand.CANCELLED if cancelled else
        EquipmentCommand.COMPLETE
    )
    command.completed_at = now
    command.save(update_fields=["status", "completed_at"])
    # An assignment can hold a leg DCMD plus an ``assign_handoff`` DCMD (H2);
    # the leg ends for all of them together. When one of them fails or is
    # cancelled while the leg DCMD is still live in Godot (e.g. Godot rejects
    # ``assign_handoff``), the leg DCMD gets a cancel DCMD (TL review B-2).
    from execution.services.handoffs import abort_handoffs_for_operation, cancel_command

    for other in assignment.commands.exclude(status__in=TERMINAL_COMMAND_STATES).exclude(
        pk=command.pk,
    ).order_by("created_at", "command_id"):
        if (failed or cancelled) and other.command_type not in {
            COMMAND_ASSIGN_HANDOFF, COMMAND_CANCEL,
        }:
            cancel_command(other, reason=reason or event)
        else:
            other.status = command.status
            other.completed_at = now
            other.save(update_fields=["status", "completed_at"])
    assignment.status = (
        DispatchAssignment.FAILED if failed else
        DispatchAssignment.CANCELLED if cancelled else
        DispatchAssignment.COMPLETE
    )
    assignment.completed_at = now
    assignment.failure_reason = reason
    assignment.save(update_fields=["status", "completed_at", "failure_reason"])
    operation.status = (
        ExecutionOperation.FAILED if failed else
        ExecutionOperation.CANCELLED if cancelled else
        ExecutionOperation.COMPLETE
    )
    operation.actual_end = now
    operation.failure_reason = reason
    operation.save(update_fields=["status", "actual_end", "failure_reason"])
    equipment.working = False
    equipment.is_dispatched = False
    equipment.is_available = equipment.powered_on and not equipment.is_in_maintenance
    # D-014/T-007: idle-since on Event/TimeMs. Results without it (REST or
    # manual path) fall back to the session's sim clock (A-6, TL review S-3).
    equipment.idle_since_event_ms = session_event_ms(command.simulation_session, event_ms)
    equipment.save(update_fields=[
        "working", "is_dispatched", "is_available", "idle_since_event_ms",
    ])

    if failed or cancelled:
        # TL review B-2: the other side of a pending hand-off can never
        # complete it; cancel the hand-off and the counterpart leg.
        abort_handoffs_for_operation(
            operation,
            reason="LEG_FAILED" if failed else "LEG_CANCELLED",
            event_ms=event_ms,
        )

    tos_event = "leg_failed" if failed else "order_cancelled" if cancelled else "leg_completed"
    outgoing = queue_execution_event(
        order, tos_event, operation=operation, equipment=equipment,
        extra={"reason": reason} if reason else None,
        occurred_at=occurred_at,
    )
    finalize_outbox_event(outgoing)
    if failed:
        order.status = ExecutionOrder.FAILED
        order.failure_reason = reason
        order.completed_at = now
        order.save(update_fields=["status", "failure_reason", "completed_at", "updated_at"])
    elif cancelled:
        order.status = ExecutionOrder.CANCELLED
        order.completed_at = now
        order.save(update_fields=["status", "completed_at", "updated_at"])
    elif not order.operations.exclude(status=ExecutionOperation.COMPLETE).exists():
        order.status = ExecutionOrder.COMPLETE
        order.completed_at = now
        order.save(update_fields=["status", "completed_at", "updated_at"])
    return command


def complete_command(command_id, *, reason=""):
    return apply_command_event(command_id, "completed", reason=reason)
