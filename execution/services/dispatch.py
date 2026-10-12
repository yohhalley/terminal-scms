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
)
from execution.services.events import finalize_outbox_event, queue_execution_event
from execution.uns import equipment_command_topic


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


def _eligible_equipment(operation):
    stale_seconds = int(getattr(settings, "EQUIPMENT_STALE_SECONDS", 60))
    query = EquipmentState.objects.select_for_update().filter(
        terminal_id=operation.order.terminal_id,
        powered_on=True,
        is_available=True,
        working=False,
        is_dispatched=False,
        is_in_maintenance=False,
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
    if equipment_id is None:
        equipment = _eligible_equipment(operation)
    else:
        equipment = EquipmentState.objects.select_for_update().filter(
            pk=equipment_id,
            terminal_id=order.terminal_id,
            powered_on=True,
            is_available=True,
            working=False,
            is_dispatched=False,
            is_in_maintenance=False,
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
    topic = equipment_command_topic(order.terminal_code, equipment.tos_che_id)
    command = EquipmentCommand.objects.create(
        assignment=assignment,
        command_type="execute_order_leg",
        topic=topic,
    )
    command.payload = {
        "schema": "scms.equipment.command.v1",
        "command_id": str(command.command_id),
        "terminal": {"id": order.terminal_id, "code": order.terminal_code},
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
            "movement_mode": operation.required_movement_mode,
        },
    }
    command.save(update_fields=["payload"])
    OutboxEvent.objects.create(
        event_id=command.command_id,
        topic=topic,
        schema="scms.equipment.command.v1",
        payload=command.payload,
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

    if event == "acknowledged":
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
