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
from execution.services.dispatch import apply_command_event
from execution.sparkplug import decode_payload, metrics_by_name, parse_topic


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
    equipment.save(update_fields=list(dict.fromkeys(changed)))
    return equipment


def _apply_command_result(metrics):
    if "Command/Result/Id" not in metrics:
        return None
    result_id = _uuid(metrics["Command/Result/Id"], "Command/Result/Id")
    existing = CommandResultReceipt.objects.filter(pk=result_id).first()
    if existing:
        return existing
    command_id = _uuid(metrics.get("Command/Result/CommandId"), "Command/Result/CommandId")
    event = str(metrics.get("Command/Result/Event", ""))
    command = EquipmentCommand.objects.select_for_update().get(pk=command_id)
    receipt = CommandResultReceipt.objects.create(
        result_id=result_id,
        command=command,
        event=event,
        raw_metrics=metrics,
    )
    position_id = metrics.get("Command/Result/PositionId")
    apply_command_event(
        command_id,
        event,
        position_id=int(position_id) if position_id not in {None, ""} else None,
        reason=str(metrics.get("Command/Result/Reason", "")),
    )
    return receipt


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
    session.save()
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
    if message_type == "NDATA":
        return _apply_command_result(metrics) or session

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
    _apply_command_result(metrics)
    return result
