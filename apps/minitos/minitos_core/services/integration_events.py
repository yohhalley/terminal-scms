import json
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from minitos_core.models import IntegrationInbox, Order
from minitos_core.services.order_execution import apply_execution_event


def _decode(payload):
    try:
        value = json.loads(payload.decode('utf-8') if isinstance(payload, bytes) else payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError('MQTT payload must be a UTF-8 JSON object.') from exc
    if not isinstance(value, dict):
        raise ValidationError('MQTT payload must be a JSON object.')
    return value


def apply_execution_message(topic, raw_payload):
    payload = _decode(raw_payload)
    if payload.get('schema') != 'scms.execution.event.v1':
        raise ValidationError('Unsupported execution-event schema.')
    try:
        event_id = uuid.UUID(str(payload['event_id']))
        terminal_id = int(payload['terminal']['id'])
        data = payload['data']
        order_id = int(data['order_id'])
        event_name = data['event']
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            'Execution event requires event_id, terminal.id, order_id, and event.'
        ) from exc

    try:
        with transaction.atomic():
            inbox, created = IntegrationInbox.objects.select_for_update().get_or_create(
                event_id=event_id,
                defaults={
                    'source': 'terminal-oes',
                    'topic': topic,
                    'schema': payload['schema'],
                    'payload': payload,
                },
            )
            order = Order.objects.select_related('work_queue').get(
                pk=order_id,
                work_queue__location_id=terminal_id,
            )
            if not created and inbox.status == IntegrationInbox.PROCESSED:
                return order
            occurred_at = parse_datetime(data.get('occurred_at', '')) or timezone.now()
            order = apply_execution_event(
                order.pk,
                event_name,
                leg_id=data.get('leg_id'),
                che_id=data.get('che_id'),
                position_id=data.get('position_id'),
                occurred_at=occurred_at,
                details=data,
            )
            inbox.status = IntegrationInbox.PROCESSED
            inbox.error = ''
            inbox.processed_at = timezone.now()
            inbox.save(update_fields=['status', 'error', 'processed_at'])
            return order
    except Exception as exc:
        IntegrationInbox.objects.update_or_create(
            event_id=event_id,
            defaults={
                'source': 'terminal-oes',
                'topic': topic,
                'schema': payload.get('schema', ''),
                'payload': payload,
                'status': IntegrationInbox.FAILED,
                'error': str(exc),
                'processed_at': timezone.now(),
            },
        )
        raise
