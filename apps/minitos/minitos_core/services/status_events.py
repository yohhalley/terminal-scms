"""TCL-003b: visit status-changed milestone events on the UNS (D-024, D-026).

Every saved status change of a CarrierVisit (vessel, truck, train or barge
visit) or a ContainerVisit queues one ``scms.visit.status_changed.v1`` event
for the MQTT UNS, including the first status when the visit is created
(``from_status`` null, e.g. the container's pre-advice). The topic is

    scms/v2/{enterprise}/{site}/tos/carrier-visits/{id}/status-changed
    scms/v2/{enterprise}/{site}/tos/container-visits/{id}/status-changed

Schema: ``contracts/schemas/scms.visit.status_changed.v1.schema.json``.

The change is detected in pre_save/post_save, so every code path that saves
the model (APIs, admin, execution events, scenario seed, shell) is covered;
``QuerySet.update()`` and ``bulk_create`` are not (no code path uses them
for these statuses today). A save with the same status publishes nothing.

The outbox row is written in ``transaction.on_commit``: a change that is
rolled back never reaches the outbox, so it is never published. The payload
(and its wall-clock ``occurred_at``) is built at save time. The outbox
publisher (``publish_uns_outbox``) sends it to MQTT as for every other
MiniTOS event.

Visits at a location that is not a Site have no UNS topic root and publish
nothing.
"""

import contextvars
import logging
import uuid
from contextlib import contextmanager

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

SCHEMA = 'scms.visit.status_changed.v1'
CARRIER_VISIT = 'carrier_visit'
CONTAINER_VISIT = 'container_visit'
TOPIC_SEGMENTS = {CARRIER_VISIT: 'carrier-visits', CONTAINER_VISIT: 'container-visits'}

# Who caused the change. Closed list, mirrored as an enum in the schema.
SOURCE_API = 'minitos.api'
SOURCE_ADMIN = 'minitos.admin'
SOURCE_SCENARIO = 'minitos.scenario'
SOURCE_SYSTEM = 'minitos.system'
SOURCE_OES_EXECUTION = 'oes.execution'
SOURCES = (SOURCE_API, SOURCE_ADMIN, SOURCE_SCENARIO, SOURCE_SYSTEM, SOURCE_OES_EXECUTION)

_UNSET = object()
_context = contextvars.ContextVar('status_event_context', default=None)


@contextmanager
def status_event_source(source, *, sim_time_ms=None, causation_id=None):
    """Tag the status changes saved inside the block with their source.

    ``sim_time_ms`` is the simulation time of the triggering event when one is
    known (an OES execution event carrying ``source_sim_time_ms``); else null.
    ``causation_id`` is the id of the triggering message, if any.
    """
    if source not in SOURCES:
        raise ValueError(f'Unknown status event source {source!r}.')
    token = _context.set({
        'source': source,
        'sim_time_ms': _int_or_none(sim_time_ms),
        'causation_id': str(causation_id) if causation_id else None,
    })
    try:
        yield
    finally:
        _context.reset(token)


def current_context():
    return _context.get() or {'source': SOURCE_SYSTEM, 'sim_time_ms': None, 'causation_id': None}


def _int_or_none(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class StatusEventSourceMiddleware:
    """Tags changes made through HTTP: the Django admin or the APIs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        source = SOURCE_ADMIN if request.path.startswith('/admin/') else SOURCE_API
        with status_event_source(source):
            return self.get_response(request)


# --- signal receivers (connected in minitos_core.signals) -----------------

def remember_stored_status(sender, instance, raw=False, update_fields=None, **kwargs):
    """pre_save: note the status in the database before this save."""
    if raw or (update_fields is not None and 'status' not in update_fields):
        instance._status_event_from = _UNSET
        return
    if instance._state.adding or instance.pk is None:
        instance._status_event_from = None
        return
    stored = sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
    instance._status_event_from = stored


def queue_status_changed(sender, instance, created=False, raw=False, **kwargs):
    """post_save: queue the event after commit if the status changed."""
    from_status = getattr(instance, '_status_event_from', _UNSET)
    instance._status_event_from = _UNSET
    if raw or from_status is _UNSET:
        return
    if not created and from_status == instance.status:
        return
    if not instance.status:
        return
    event = build_event(instance, from_status)
    if event is None:
        return
    transaction.on_commit(lambda: _write_outbox(event), robust=True)


def _write_outbox(event):
    from minitos_core.models import IntegrationOutbox

    try:
        IntegrationOutbox.objects.create(**event)
    except Exception:
        # The status change is already committed; robust on_commit keeps the
        # caller's response intact, so log the lost event loudly.
        logger.exception('Could not queue %s event %s (%s)', SCHEMA, event['event_id'], event['topic'])


def _entity_type(instance):
    from minitos_core.models import CarrierVisit, ContainerVisit

    if isinstance(instance, CarrierVisit):
        return CARRIER_VISIT
    if isinstance(instance, ContainerVisit):
        return CONTAINER_VISIT
    raise TypeError(f'No status events for {type(instance).__name__}.')


def _site(location_id):
    from minitos_core.models import Site

    if not location_id:
        return None
    return Site.objects.select_related('enterprise', 'location').filter(location_id=location_id).first()


def _carrier_ref(visit_id):
    from minitos_core.models import CarrierVisit

    if not visit_id:
        return None
    visit = CarrierVisit.objects.filter(pk=visit_id).values('pk', 'name', 'visit_type').first()
    if visit is None:
        return None
    return {'id': visit['pk'], 'name': visit['name'], 'visit_type': visit['visit_type']}


def _entity_data(entity_type, instance):
    if entity_type == CARRIER_VISIT:
        return instance.name, {
            'visit_type': instance.visit_type,
            'vessel_name': instance.vessel_name or None,
        }
    # D-010: the visit id plus the ISO 6346 container number, category, carriers.
    return instance.container_number_id, {
        'container_number': instance.container_number_id,
        'category': instance.category,
        'ib_carrier': _carrier_ref(instance.ib_carrier_id),
        'ob_carrier': _carrier_ref(instance.ob_carrier_id),
    }


def build_event(instance, from_status):
    """The outbox row (topic, schema, payload) for one status change, or None."""
    entity_type = _entity_type(instance)
    site = _site(instance.location_id)
    if site is None:
        return None
    context = current_context()
    event_id = uuid.uuid4()
    occurred_at = timezone.now()
    code, details = _entity_data(entity_type, instance)
    root = getattr(settings, 'SITE_UNS_ROOT', 'scms/v2').strip('/')
    topic = (
        f'{root}/{site.enterprise.code}/{site.code}/tos/'
        f'{TOPIC_SEGMENTS[entity_type]}/{instance.pk}/status-changed'
    )
    payload = {
        'schema': SCHEMA,
        'event_id': str(event_id),
        'occurred_at': occurred_at.isoformat(),
        'enterprise': {
            'id': str(site.enterprise_id),
            'code': site.enterprise.code,
            'name': site.enterprise.name,
        },
        'site': {
            'id': str(site.pk),
            'code': site.code,
            'name': site.location.name,
            'source_location_id': site.location_id,
        },
        'terminal': {'id': site.location_id, 'code': f'terminal-{site.location_id}'},
        'data': {
            'entity_type': entity_type,
            'entity_id': instance.pk,
            'entity_code': code,
            'from_status': from_status,
            'to_status': instance.status,
            'sim_time_ms': context['sim_time_ms'],
            'source': context['source'],
            'causation_id': context['causation_id'],
            entity_type: details,
        },
    }
    return {'event_id': event_id, 'topic': topic, 'schema': SCHEMA, 'payload': payload, 'qos': 1}
