from django.utils import timezone

from execution.models import OutboxEvent
from execution.uns import execution_event_topic


def queue_execution_event(
    order, event, *, operation=None, equipment=None, extra=None,
    occurred_at=None, event_id=None,
):
    """Queue one ``scms.execution.event.v1`` business event.

    ``occurred_at`` is the event time (contract §2.1, from ``Event/TimeMs``)
    when the caller knows it; ``event_id`` lets a caller make the event id
    deterministic (``container_transferred`` uses uuid5 of the HandoffId).
    """
    data = {
        "event": event,
        "order_id": order.tos_order_id,
        "occurred_at": (occurred_at or timezone.now()).isoformat(),
    }
    if operation:
        data["leg_id"] = operation.tos_leg_id
    if equipment:
        data["che_id"] = equipment.tos_che_id
    if extra:
        data.update(extra)
    identity = {"event_id": event_id} if event_id else {}
    return OutboxEvent.objects.create(
        **identity,
        topic=execution_event_topic(order.site, order.tos_order_id),
        schema="scms.execution.event.v1",
        payload={
            "schema": "scms.execution.event.v1",
            "event_id": None,
            "enterprise": {
                "id": str(order.site.enterprise.source_id),
                "code": order.site.enterprise.code,
                "name": order.site.enterprise.name,
            },
            "site": {
                "id": str(order.site.source_id),
                "code": order.site.code,
                "name": order.site.name,
                "source_location_id": order.site.source_location_id,
                "configuration_revision": order.site.source_revision,
            },
            # B3: MiniTOS apply_execution_message reads terminal.id (its Location pk).
            "terminal": {
                "id": order.site.source_location_id,
                "code": order.site.code,
            },
            "data": data,
        },
        qos=1,
    )


def finalize_outbox_event(event):
    event.payload["event_id"] = str(event.event_id)
    event.save(update_fields=["payload"])
    return event
