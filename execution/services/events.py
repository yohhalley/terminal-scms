from django.utils import timezone

from execution.models import OutboxEvent
from execution.uns import execution_event_topic


def queue_execution_event(order, event, *, operation=None, equipment=None, extra=None):
    data = {
        "event": event,
        "order_id": order.tos_order_id,
        "occurred_at": timezone.now().isoformat(),
    }
    if operation:
        data["leg_id"] = operation.tos_leg_id
    if equipment:
        data["che_id"] = equipment.tos_che_id
    if extra:
        data.update(extra)
    return OutboxEvent.objects.create(
        topic=execution_event_topic(order.terminal_code, order.tos_order_id),
        schema="scms.execution.event.v1",
        payload={
            "schema": "scms.execution.event.v1",
            "event_id": None,
            "terminal": {"id": order.terminal_id, "code": order.terminal_code},
            "data": data,
        },
        qos=1,
    )


def finalize_outbox_event(event):
    event.payload["event_id"] = str(event.event_id)
    event.save(update_fields=["payload"])
    return event

