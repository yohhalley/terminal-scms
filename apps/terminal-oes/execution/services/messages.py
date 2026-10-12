import json
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from execution.models import InboxEvent
from execution.services.dispatch import apply_command_event
from execution.services.ingestion import ingest_equipment_state, ingest_released_order


def _decode(payload):
    try:
        value = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("MQTT payload must be a UTF-8 JSON object.") from exc
    if not isinstance(value, dict):
        raise ValidationError("MQTT payload must be a JSON object.")
    return value


def _process_command_result(topic, payload):
    if payload.get("schema") != "scms.command.result.v1":
        raise ValidationError("Unsupported command-result schema.")
    try:
        event_id = uuid.UUID(str(payload["event_id"]))
        data = payload["data"]
        command_id = uuid.UUID(str(data["command_id"]))
        event = data["event"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Command result requires event_id, command_id and event.") from exc

    with transaction.atomic():
        inbox, created = InboxEvent.objects.select_for_update().get_or_create(
            event_id=event_id,
            defaults={
                "topic": topic,
                "schema": payload["schema"],
                "payload": payload,
            },
        )
        if not created and inbox.status == InboxEvent.PROCESSED:
            return inbox
        apply_command_event(
            command_id,
            event,
            position_id=data.get("position_id"),
            reason=data.get("reason", ""),
        )
        inbox.status = InboxEvent.PROCESSED
        inbox.error = ""
        inbox.processed_at = timezone.now()
        inbox.save(update_fields=["status", "error", "processed_at"])
        return inbox


def process_message(topic, raw_payload):
    payload = _decode(raw_payload)
    schema = payload.get("schema")
    if schema == "scms.order.released.v1":
        return ingest_released_order(topic, payload)
    if schema == "scms.equipment.state.v1":
        return ingest_equipment_state(topic, payload)
    if schema == "scms.command.result.v1":
        return _process_command_result(topic, payload)
    raise ValidationError(f"Unsupported UNS message schema: {schema!r}.")

