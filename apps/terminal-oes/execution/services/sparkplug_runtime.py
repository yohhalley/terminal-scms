import logging
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from execution.models import (
    CommandResultReceipt,
    EquipmentCommand,
    EquipmentState,
    SimulationSession,
)
from execution.services.dispatch import COMMAND_CANCEL, apply_command_event
from execution.services.handoffs import (
    ResultRejected,
    apply_handoff_completed,
    apply_handoff_timed_out,
    close_session_handoffs,
    record_conflict,
)
from execution.sparkplug import decode_payload, metrics_by_name, parse_topic

logger = logging.getLogger(__name__)

# Results Godot publishes for observability only (contract §3.3, §4 steps 5/6/11).
INFORMATIONAL_EVENTS = {
    "arrived_at_source",
    "arrived_at_destination",
    "waiting_for_handoff",
    "wait_started",
}


NODE_ID_METRIC = "Session/Id"
SITE_ID_METRIC = "Site/Id"
REVISION_METRIC = "Configuration/Revision"
CHECKSUM_METRIC = "Configuration/Checksum"


def _uuid(value, label):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be a UUID.") from exc


def _active_session(topic_parts):
    try:
        return (
            SimulationSession.objects.select_for_update()
            .select_related("site__enterprise")
            .get(
                sparkplug_group_id=topic_parts["group_id"],
                sparkplug_node_id=topic_parts["node_id"],
                actual_state__in=SimulationSession.ACTIVE_STATES,
            )
        )
    except SimulationSession.DoesNotExist as exc:
        raise ValidationError("Sparkplug message does not belong to an active session.") from exc


def _track_sequence(session, sequence, *, reset=False):
    if sequence is None:
        raise ValidationError("Sparkplug lifecycle and data messages require seq.")
    sequence = int(sequence)
    if not 0 <= sequence <= 255:
        raise ValidationError("Sparkplug seq must be between 0 and 255.")
    if reset:
        if sequence != 0:
            raise ValidationError("NBIRTH must reset seq to zero.")
    elif session.sparkplug_last_sequence is not None:
        expected = (session.sparkplug_last_sequence + 1) % 256
        if sequence != expected:
            session.sequence_gap_count += 1
    session.sparkplug_last_sequence = sequence


def _require_session_identity(session, metrics):
    if _uuid(metrics.get(NODE_ID_METRIC), NODE_ID_METRIC) != session.pk:
        raise ValidationError("NBIRTH session identity does not match the active session.")
    if _uuid(metrics.get(SITE_ID_METRIC), SITE_ID_METRIC) != session.site.source_id:
        raise ValidationError("NBIRTH Site identity does not match the active session.")
    if int(metrics.get(REVISION_METRIC, -1)) != session.configuration_revision:
        raise ValidationError("NBIRTH configuration revision does not match the session.")
    if str(metrics.get(CHECKSUM_METRIC, "")) != session.configuration_checksum:
        raise ValidationError("NBIRTH configuration checksum does not match the session.")


def _equipment_for_device(session, device_id, metrics=None):
    metrics = metrics or {}
    identity = metrics.get("Identity/Id")
    query = EquipmentState.objects.select_for_update().filter(site=session.site)
    if identity is not None:
        try:
            return query.get(tos_che_id=int(identity))
        except (TypeError, ValueError, EquipmentState.DoesNotExist) as exc:
            raise ValidationError("DBIRTH references an unknown CHE identity.") from exc
    try:
        return query.get(sparkplug_device_id=device_id)
    except EquipmentState.DoesNotExist as exc:
        raise ValidationError("Sparkplug Device ID is not born for this Site.") from exc


def _apply_device_metrics(equipment, session, metrics, sequence, *, birth=False):
    field_metrics = {
        "State/PoweredOn": ("powered_on", bool),
        "State/Working": ("working", bool),
        "State/IsAvailable": ("is_available", bool),
        "State/IsDispatched": ("is_dispatched", bool),
        "State/InMaintenance": ("is_in_maintenance", bool),
        "Position/Latitude": ("latitude", float),
        "Position/Longitude": ("longitude", float),
        "Position/HeadingTrueNorthDeg": ("rotation", float),
        "Fuel/LevelPercent": ("fuel_level", float),
        "Energy/LevelPercent": ("energy_level", float),
        "Motion/DrivingSpeedMps": ("speed_mps", float),
    }
    changed = []
    for metric_name, (field_name, caster) in field_metrics.items():
        if metric_name not in metrics:
            continue
        value = metrics[metric_name]
        if value is not None:
            setattr(equipment, field_name, caster(value))
            changed.append(field_name)
    now = timezone.now()
    equipment.simulation_session = session
    equipment.device_connected = True
    equipment.data_valid = True
    equipment.sparkplug_last_sequence = sequence
    equipment.sparkplug_bdseq = session.sparkplug_bdseq
    equipment.source_timestamp = now
    equipment.last_seen_at = now
    equipment.state_source = EquipmentState.SIMULATOR
    equipment.raw_state = metrics
    changed.extend([
        "simulation_session", "device_connected", "data_valid",
        "sparkplug_last_sequence", "sparkplug_bdseq", "source_timestamp",
        "last_seen_at", "state_source", "raw_state",
    ])
    if birth:
        equipment.last_birth_at = now
        changed.append("last_birth_at")
        if equipment.idle_since_event_ms is None and session.start_epoch_ms is not None:
            # D-014: a CHE never given an order is idle since the run started.
            equipment.idle_since_event_ms = session.start_epoch_ms
            changed.append("idle_since_event_ms")
    equipment.save(update_fields=list(dict.fromkeys(changed)))
    return equipment


def _optional_int(metrics, name):
    value = metrics.get(name)
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _route_result(command, event, metrics, result_id):
    """Apply one result. Returns the receipt status; raises ``ValidationError`` to reject."""
    event_ms = _optional_int(metrics, "Event/TimeMs")
    sim_ms = _optional_int(metrics, "Sim/TimeMs")
    operation = command.assignment.operation
    if command.command_type == COMMAND_CANCEL:
        # TCL-014a: only a failed cancel is reported on the cancel's own id;
        # the cancelled command gets ``cancelled`` (HeadlessSimulator.gd `_handle_cancel`).
        logger.warning("Cancel DCMD %s reported %s: %s", command.pk, event,
                       metrics.get("Command/Result/Reason", ""))
        return CommandResultReceipt.INFORMATIONAL
    if event in INFORMATIONAL_EVENTS:
        return CommandResultReceipt.INFORMATIONAL
    expected_number = operation.order.container_number
    reported_number = str(metrics.get("Command/Result/ContainerNumber") or "")
    if expected_number and reported_number and reported_number != expected_number:
        raise ResultRejected("container_number_mismatch")
    if event == "handoff_completed":
        apply_handoff_completed(command, metrics, result_id=result_id, event_ms=event_ms)
        return CommandResultReceipt.APPLIED
    if event == "handoff_timed_out":
        raised = apply_handoff_timed_out(command, metrics, event_ms=event_ms, sim_ms=sim_ms)
        return CommandResultReceipt.APPLIED if raised else CommandResultReceipt.INFORMATIONAL
    if event == "container_picked_up" and operation.incoming_handoffs.exists():
        # The receiver's pick is the hand-off itself; custody moves with
        # container_transferred, not with a second pick event.
        return CommandResultReceipt.INFORMATIONAL
    if event == "container_dropped" and operation.outgoing_handoffs.exists():
        raise ResultRejected("handoff_giver_drop", "A hand-off giver does not drop (Q8-R5).")
    position_id = _optional_int(metrics, "Command/Result/PositionId")
    apply_command_event(
        command.pk,
        event,
        position_id=position_id or None,
        reason=str(metrics.get("Command/Result/Reason", "")),
        event_ms=event_ms,
        source=metrics.get("Command/Result/Source") or None,
    )
    return CommandResultReceipt.APPLIED


def _apply_command_result(metrics, session=None):
    """B4/B5: apply a Godot result once per ``Command/Result/Id``.

    A repeat is a no-op. A result that cannot be applied is stored as a
    ``rejected`` receipt with a reason and logged, and never rolls back the
    DDATA that carried it (telemetry and sequence tracking still apply).
    """
    if "Command/Result/Id" not in metrics:
        return None
    result_id = _uuid(metrics["Command/Result/Id"], "Command/Result/Id")
    existing = CommandResultReceipt.objects.filter(pk=result_id).first()
    if existing:
        if session is not None and existing.simulation_session_id not in {None, session.pk}:
            logger.warning("Result id %s reused across sessions; ignored.", result_id)
        return existing
    command_id = _uuid(metrics.get("Command/Result/CommandId"), "Command/Result/CommandId")
    event = str(metrics.get("Command/Result/Event", ""))
    command = EquipmentCommand.objects.select_for_update().select_related(
        "assignment__operation__order", "assignment__equipment",
    ).filter(pk=command_id).first()
    if command is None:
        logger.warning("Result %s names unknown command %s.", result_id, command_id)
        return None
    status = CommandResultReceipt.APPLIED
    reason = ""
    try:
        with transaction.atomic():
            status = _route_result(command, event, metrics, result_id)
    except ResultRejected as exc:
        status, reason = CommandResultReceipt.REJECTED, exc.reason_code
        if exc.conflict:
            record_conflict(exc.conflict)
    except ValidationError as exc:
        status, reason = CommandResultReceipt.REJECTED, "; ".join(exc.messages)[:120]
    if status == CommandResultReceipt.REJECTED:
        logger.warning("Result %s (%s) for command %s rejected: %s",
                       result_id, event, command_id, reason)
    return CommandResultReceipt.objects.create(
        result_id=result_id,
        command=command,
        simulation_session=session or command.simulation_session,
        event=event[:40],
        status=status,
        reason=reason,
        event_seq=_optional_int(metrics, "Sim/EventSeq"),
        sim_time_ms=_optional_int(metrics, "Sim/TimeMs"),
        raw_metrics=metrics,
    )


def _is_stale(session, metrics):
    """B5: a DDATA from another session (late replay after a restart) is dropped."""
    reported = metrics.get("Session/Id")
    if reported in {None, ""}:
        return False  # assumption A-7: older publishers omit Session/Id
    try:
        return uuid.UUID(str(reported)) != session.pk
    except ValueError:
        return True


def _advance_sim_clock(session, metrics):
    sim_ms = _optional_int(metrics, "Sim/TimeMs")
    if sim_ms is not None and sim_ms > session.last_sim_time_ms:
        session.last_sim_time_ms = sim_ms
        session.save(update_fields=["last_sim_time_ms", "updated_at"])


def _handle_nbirth(session, document, metrics):
    _require_session_identity(session, metrics)
    if "bdSeq" not in metrics:
        raise ValidationError("NBIRTH requires bdSeq.")
    _track_sequence(session, document.get("seq"), reset=True)
    now = timezone.now()
    session.sparkplug_bdseq = int(metrics["bdSeq"])
    session.node_connected = True
    session.actual_state = SimulationSession.RUNNING
    session.started_at = session.started_at or now
    session.heartbeat_at = now
    session.last_node_birth_at = now
    session.failure_reason = ""
    # D-014 tie-break seed and the Event/TimeMs origin (contract §2.1, NBIRTH only).
    seed = _optional_int(metrics, "Sim/Seed")
    if seed is not None:
        session.seed = seed
    start_epoch = _optional_int(metrics, "Sim/StartEpochMs")
    if start_epoch is not None:
        session.start_epoch_ms = start_epoch
    command_ttl_s = _optional_int(metrics, "Sim/Timing/CommandTtlS")
    session.command_ttl_s = command_ttl_s if command_ttl_s and command_ttl_s > 0 else None
    session.save()
    # TL review B-2: hand-offs left pending by an abandoned earlier session of
    # this site are closed, so they never lock a CHE out of this run.
    close_session_handoffs(
        SimulationSession.objects.filter(site=session.site).exclude(pk=session.pk),
        reason="SESSION_ABANDONED",
    )
    # D-014/D-016 (TL review B-1): idle time is per session. CHE rows are reused
    # across sessions of a site, so a CHE first bound to this session restarts
    # idle at Sim/StartEpochMs (None when unknown; DBIRTH seeds it then). A
    # rebirth of the same session keeps the idle times it already has.
    EquipmentState.objects.filter(site=session.site).exclude(
        simulation_session=session,
    ).update(idle_since_event_ms=session.start_epoch_ms)
    EquipmentState.objects.filter(site=session.site).update(
        simulation_session=session,
        device_connected=False,
        data_valid=False,
    )
    return session


def _handle_ndeath(session, metrics):
    if "bdSeq" not in metrics or int(metrics["bdSeq"]) != session.sparkplug_bdseq:
        raise ValidationError("NDEATH bdSeq does not match the current node birth.")
    now = timezone.now()
    session.node_connected = False
    session.last_node_death_at = now
    session.heartbeat_at = now
    if session.desired_state == SimulationSession.STOPPED_DESIRED:
        session.actual_state = SimulationSession.STOPPED
        session.stopped_at = now
        close_session_handoffs(session, reason="SESSION_STOPPED")
    else:
        session.actual_state = SimulationSession.STARTING
        session.failure_reason = "Sparkplug Edge Node is offline; awaiting a new NBIRTH."
    session.save()
    EquipmentState.objects.filter(simulation_session=session).update(
        device_connected=False,
        data_valid=False,
        working=False,
        is_dispatched=False,
        is_available=False,
        last_death_at=now,
    )
    return session


@transaction.atomic
def process_sparkplug_message(topic, raw_payload):
    topic_parts = parse_topic(topic)
    document = decode_payload(raw_payload)
    metrics = metrics_by_name(document)
    session = _active_session(topic_parts)
    message_type = topic_parts["message_type"]

    if message_type == "NBIRTH":
        return _handle_nbirth(session, document, metrics)
    if message_type == "NDEATH":
        return _handle_ndeath(session, metrics)
    if not session.node_connected:
        raise ValidationError("Device data is invalid until a matching NBIRTH is received.")

    _track_sequence(session, document.get("seq"))
    session.heartbeat_at = timezone.now()
    session.save(update_fields=[
        "sparkplug_last_sequence", "sequence_gap_count", "heartbeat_at", "updated_at",
    ])
    if message_type in {"NDATA", "DDATA"} and _is_stale(session, metrics):
        session.stale_message_count += 1
        session.save(update_fields=["stale_message_count", "updated_at"])
        logger.warning("Dropped %s from another session on %s.", message_type, topic)
        return session
    _advance_sim_clock(session, metrics)
    if message_type == "NDATA":
        return _apply_command_result(metrics, session) or session

    device_id = topic_parts["device_id"]
    equipment = _equipment_for_device(
        session,
        device_id,
        metrics if message_type == "DBIRTH" else None,
    )
    if message_type == "DDEATH":
        now = timezone.now()
        equipment.device_connected = False
        equipment.data_valid = False
        equipment.working = False
        equipment.is_dispatched = False
        equipment.is_available = False
        equipment.last_death_at = now
        equipment.last_seen_at = now
        equipment.save(update_fields=[
            "device_connected", "data_valid", "working", "is_dispatched",
            "is_available", "last_death_at", "last_seen_at",
        ])
        return equipment
    if message_type == "DBIRTH":
        equipment.sparkplug_device_id = device_id
        equipment.save(update_fields=["sparkplug_device_id"])
    result = _apply_device_metrics(
        equipment,
        session,
        metrics,
        document["seq"],
        birth=message_type == "DBIRTH",
    )
    _apply_command_result(metrics, session)
    return result
