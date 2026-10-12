import uuid
from datetime import timezone as datetime_timezone

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from execution.models import EquipmentState, ExecutionOperation, ExecutionOrder, InboxEvent


def _timestamp(value, *, required=False):
    if value is None or value == "":
        if required:
            raise ValidationError("A timestamp is required.")
        return None
    if hasattr(value, "tzinfo"):
        parsed = value
    else:
        parsed = parse_datetime(str(value))
    if parsed is None:
        raise ValidationError(f"Invalid ISO-8601 timestamp: {value}")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def _event_id(payload):
    try:
        return uuid.UUID(str(payload["event_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("The event envelope requires a valid event_id UUID.") from exc


def _terminal(payload):
    terminal = payload.get("terminal") or {}
    try:
        return int(terminal["id"]), str(terminal["code"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("The event envelope requires terminal.id and terminal.code.") from exc


def _record_failure(event_id, topic, payload, exc):
    InboxEvent.objects.update_or_create(
        event_id=event_id,
        defaults={
            "topic": topic,
            "schema": payload.get("schema", ""),
            "payload": payload,
            "status": InboxEvent.FAILED,
            "error": str(exc),
            "processed_at": timezone.now(),
        },
    )


def ingest_released_order(topic, payload):
    event_id = _event_id(payload)
    existing = InboxEvent.objects.filter(event_id=event_id, status=InboxEvent.PROCESSED).first()
    if existing:
        order_id = (existing.payload.get("data") or {}).get("order", {}).get("id")
        return ExecutionOrder.objects.get(tos_order_id=order_id)

    try:
        with transaction.atomic():
            terminal_id, terminal_code = _terminal(payload)
            data = payload.get("data") or {}
            source_order = data.get("order") or {}
            legs = data.get("legs") or []
            if payload.get("schema") != "scms.order.released.v1":
                raise ValidationError("Unsupported released-order schema.")
            if source_order.get("status") != ExecutionOrder.RELEASED:
                raise ValidationError("Only released TOS orders can enter the OES.")
            if not legs:
                raise ValidationError("A released order requires at least one operation.")

            order_id = int(source_order["id"])
            order, created = ExecutionOrder.objects.select_for_update().get_or_create(
                tos_order_id=order_id,
                defaults={
                    "terminal_id": terminal_id,
                    "terminal_code": terminal_code,
                    "released_at": _timestamp(source_order.get("released_at"), required=True),
                },
            )
            if not created and order.terminal_id != terminal_id:
                raise ValidationError("The TOS order is already owned by another terminal.")
            if order.status not in {ExecutionOrder.RELEASED, ExecutionOrder.PLANNED}:
                return order

            order.terminal_code = terminal_code
            order.status = ExecutionOrder.RELEASED
            order.priority = int(source_order.get("priority", 100))
            order.container_visit_id = source_order.get("container_number")
            order.from_position_id = source_order.get("from_position")
            order.to_position_id = source_order.get("to_position")
            order.released_at = _timestamp(source_order.get("released_at"), required=True)
            order.source_payload = source_order
            order.save()

            seen_sequences = set()
            for leg in legs:
                sequence = int(leg["sequence"])
                if sequence in seen_sequences:
                    raise ValidationError(f"Duplicate operation sequence {sequence}.")
                seen_sequences.add(sequence)
                ExecutionOperation.objects.update_or_create(
                    order=order,
                    tos_leg_id=int(leg["id"]),
                    defaults={
                        "sequence": sequence,
                        "kind": leg.get("leg_kind", ExecutionOperation.MOVE),
                        "status": ExecutionOperation.PLANNED,
                        "required_equipment_type_id": leg.get("equipment_type"),
                        "required_movement_mode": leg.get("movement_mode", ""),
                        "source_position_id": leg.get("start_position"),
                        "destination_position_id": leg.get("end_position"),
                        "source_inventory_position_id": leg.get("source_inventory_position_id"),
                        "destination_inventory_position_id": leg.get(
                            "destination_inventory_position_id"
                        ),
                        "planned_start": _timestamp(leg.get("planned_start")),
                        "planned_end": _timestamp(leg.get("planned_end")),
                        "source_payload": leg,
                    },
                )
            order.operations.exclude(sequence__in=seen_sequences).delete()
            InboxEvent.objects.update_or_create(
                event_id=event_id,
                defaults={
                    "topic": topic,
                    "schema": payload["schema"],
                    "payload": payload,
                    "status": InboxEvent.PROCESSED,
                    "error": "",
                    "processed_at": timezone.now(),
                },
            )
            return order
    except Exception as exc:
        _record_failure(event_id, topic, payload, exc)
        raise


@transaction.atomic
def ingest_equipment_state(topic, payload):
    if payload.get("schema") != "scms.equipment.state.v1":
        raise ValidationError("Unsupported equipment-state schema.")
    terminal_id, terminal_code = _terminal(payload)
    data = payload.get("data") or {}
    try:
        che_id = int(data["che_id"])
        name = str(data["name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("Equipment state requires data.che_id and data.name.") from exc
    source_timestamp = _timestamp(data.get("timestamp") or payload.get("occurred_at"))
    equipment, _ = EquipmentState.objects.update_or_create(
        terminal_id=terminal_id,
        tos_che_id=che_id,
        defaults={
            "terminal_code": terminal_code,
            "name": name,
            "equipment_type_id": data.get("equipment_type_id"),
            "equipment_type_code": data.get("equipment_type_code", ""),
            "powered_on": bool(data.get("powered_on", False)),
            "working": bool(data.get("working", False)),
            "is_available": bool(data.get("is_available", False)),
            "is_dispatched": bool(data.get("is_dispatched", False)),
            "is_in_maintenance": bool(data.get("is_in_maintenance", False)),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "rotation": float(data.get("rotation", 0.0)),
            "fuel_level": data.get("fuel_level"),
            "source_timestamp": source_timestamp,
            "last_seen_at": timezone.now(),
            "raw_state": data,
        },
    )
    return equipment
