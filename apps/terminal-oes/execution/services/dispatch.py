from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from execution.models import (
    DispatchAssignment,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    OutboxEvent,
    SimulationSession,
)
from execution.services.events import finalize_outbox_event, queue_execution_event
from execution.sparkplug import encode_payload


def _next_operation(order):
    operations = list(order.operations.select_for_update().order_by("sequence"))
    for index, operation in enumerate(operations):
        if operation.status == ExecutionOperation.COMPLETE:
            continue
        if operation.status != ExecutionOperation.PLANNED:
            raise ValidationError("The next order operation is already assigned or blocked.")
        if any(previous.status != ExecutionOperation.COMPLETE for previous in operations[:index]):
            raise ValidationError("An earlier order operation is not complete.")
        return operation
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
    return query.order_by("last_seen_at", "tos_che_id").first()


@transaction.atomic
def dispatch_next_operation(order_id, *, equipment_id=None):
    order = ExecutionOrder.objects.select_for_update().get(pk=order_id)
    if order.status not in {ExecutionOrder.RELEASED, ExecutionOrder.IN_PROGRESS}:
        raise ValidationError("Only released or active orders can be dispatched.")
    operation = _next_operation(order)
    session = SimulationSession.objects.select_for_update().filter(
        site=order.site,
        desired_state=SimulationSession.RUNNING_DESIRED,
        actual_state=SimulationSession.RUNNING,
        node_connected=True,
    ).first()
    if session is None:
        raise ValidationError("The Site has no connected, running Sparkplug simulation.")
    if equipment_id is None:
        equipment = _eligible_equipment(operation, session)
    else:
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
    if equipment is None:
        raise ValidationError("No fresh, available CHE satisfies this operation.")

    assignment = DispatchAssignment.objects.create(
        operation=operation,
        equipment=equipment,
    )
    if equipment.simulation_session_id != session.pk:
        raise ValidationError("The selected CHE does not belong to the active simulation session.")
    device_id = equipment.sparkplug_device_id or f"CHE_{equipment.tos_che_id}"
    topic = "spBv1.0/{}/DCMD/{}/{}".format(
        session.sparkplug_group_id,
        session.sparkplug_node_id,
        device_id,
    )
    expires_at = timezone.now() + timedelta(
        seconds=int(getattr(settings, "EQUIPMENT_COMMAND_TTL_SECONDS", 30)),
    )
    command = EquipmentCommand.objects.create(
        assignment=assignment,
        simulation_session=session,
        command_type="execute_order_leg",
        topic=topic,
        expires_at=expires_at,
    )
    command.payload = {
        "schema": "scms.equipment.command.v1",
        "command_id": str(command.command_id),
        "site": {"id": str(order.site.source_id), "code": order.site.code},
        "session": {
            "id": str(session.pk),
            "configuration_revision": session.configuration_revision,
            "expires_at": expires_at.isoformat(),
        },
        "data": {
            "command": command.command_type,
            "order_id": order.tos_order_id,
            "leg_id": operation.tos_leg_id,
            "che_id": equipment.tos_che_id,
            "container_visit_id": order.container_visit_id,
            "source_position_id": operation.source_position_id,
            "destination_position_id": operation.destination_position_id,
            "source_inventory_position_id": operation.source_inventory_position_id,
            "destination_inventory_position_id": operation.destination_inventory_position_id,
            "source_latitude": operation.source_latitude,
            "source_longitude": operation.source_longitude,
            "destination_latitude": operation.destination_latitude,
            "destination_longitude": operation.destination_longitude,
            "movement_mode": operation.required_movement_mode,
        },
    }
    command.save(update_fields=["payload"])
    data = command.payload["data"]
    metrics = [
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
        {
            "name": "Command/ExpiresAtMs",
            "type": "DateTime",
            "value": int(expires_at.timestamp() * 1000),
        },
        {"name": "Command/CheId", "type": "Int64", "value": equipment.tos_che_id},
        {"name": "Command/OrderId", "type": "Int64", "value": order.tos_order_id},
        {"name": "Command/LegId", "type": "Int64", "value": operation.tos_leg_id},
        {
            "name": "Command/MovementMode",
            "type": "String",
            "value": operation.required_movement_mode,
        },
    ]
    optional_metrics = [
        ("Command/ContainerVisitId", "Int64", data.get("container_visit_id")),
        ("Command/SourcePositionId", "Int64", data.get("source_position_id")),
        ("Command/DestinationPositionId", "Int64", data.get("destination_position_id")),
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
    OutboxEvent.objects.create(
        event_id=command.command_id,
        topic=topic,
        schema="scms.equipment.command.v1",
        payload=command.payload,
        payload_bytes=encode_payload(metrics, sequence=0),
        qos=1,
    )

    operation.status = ExecutionOperation.DISPATCHED
    operation.save(update_fields=["status"])
    equipment.is_dispatched = True
    equipment.is_available = False
    equipment.save(update_fields=["is_dispatched", "is_available"])
    if order.status == ExecutionOrder.RELEASED:
        order.status = ExecutionOrder.IN_PROGRESS
        order.started_at = timezone.now()
        order.save(update_fields=["status", "started_at", "updated_at"])
    event = queue_execution_event(
        order, "leg_dispatched", operation=operation, equipment=equipment,
    )
    finalize_outbox_event(event)
    return assignment


@transaction.atomic
def apply_command_event(command_id, event, *, position_id=None, reason=""):
    command = EquipmentCommand.objects.select_for_update().select_related(
        "assignment__operation__order", "assignment__equipment",
    ).get(pk=command_id)
    assignment = command.assignment
    operation = assignment.operation
    order = operation.order
    equipment = assignment.equipment
    now = timezone.now()

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
        )
        finalize_outbox_event(outgoing)
        return command
    if event in {"container_picked_up", "container_dropped"}:
        resolved_position_id = position_id
        if event == "container_dropped" and resolved_position_id is None:
            resolved_position_id = operation.destination_inventory_position_id
        if event == "container_dropped" and resolved_position_id is None:
            raise ValidationError("container_dropped requires an inventory position.")
        extra = {"position_id": resolved_position_id} if resolved_position_id else None
        outgoing = queue_execution_event(
            order, event, operation=operation, equipment=equipment, extra=extra,
        )
        finalize_outbox_event(outgoing)
        return command
    if event not in {"completed", "failed", "cancelled"}:
        raise ValidationError(f"Unsupported command event: {event}.")

    failed = event == "failed"
    cancelled = event == "cancelled"
    command.status = (
        EquipmentCommand.FAILED if failed else
        EquipmentCommand.CANCELLED if cancelled else
        EquipmentCommand.COMPLETE
    )
    command.completed_at = now
    command.save(update_fields=["status", "completed_at"])
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
    equipment.save(update_fields=["working", "is_dispatched", "is_available"])

    tos_event = "leg_failed" if failed else "order_cancelled" if cancelled else "leg_completed"
    outgoing = queue_execution_event(
        order, tos_event, operation=operation, equipment=equipment,
        extra={"reason": reason} if reason else None,
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
