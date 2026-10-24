import hashlib
import json
import uuid
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from execution.models import (
    EnterpriseProjection,
    EquipmentState,
    InboxEvent,
    SiteProjection,
)


def _event_id(payload):
    try:
        return uuid.UUID(str(payload['event_id']))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError('The Site event requires a valid event_id UUID.') from exc


def _identity(payload):
    enterprise = payload.get('enterprise') or {}
    site = payload.get('site') or {}
    try:
        return {
            'enterprise_id': uuid.UUID(str(enterprise['id'])),
            'enterprise_code': str(enterprise['code']),
            'enterprise_name': str(enterprise['name']),
            'site_id': uuid.UUID(str(site['id'])),
            'site_code': str(site['code']),
            'site_name': str(site['name']),
            'source_location_id': int(site['source_location_id']),
            'location_type': str(site['location_type']),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            'Site events require enterprise and site UUID, code, name, and source location.'
        ) from exc


def _record_inbox(event_id, topic, payload, status, error=''):
    InboxEvent.objects.update_or_create(
        event_id=event_id,
        defaults={
            'topic': topic,
            'schema': payload.get('schema', ''),
            'payload': payload,
            'status': status,
            'error': error,
            'processed_at': timezone.now(),
        },
    )


def _project_identity(payload):
    identity = _identity(payload)
    enterprise, _ = EnterpriseProjection.objects.update_or_create(
        source_id=identity['enterprise_id'],
        defaults={
            'code': identity['enterprise_code'],
            'name': identity['enterprise_name'],
            'is_active': True,
            'source_payload': payload.get('enterprise') or {},
        },
    )
    site, _ = SiteProjection.objects.update_or_create(
        source_id=identity['site_id'],
        defaults={
            'enterprise': enterprise,
            'source_location_id': identity['source_location_id'],
            'code': identity['site_code'],
            'name': identity['site_name'],
            'lifecycle_status': (payload.get('data') or {}).get('lifecycle_status', 'draft'),
            'simulation_enabled': bool(
                (payload.get('data') or {}).get('simulation_enabled', True)
            ),
            'source_payload': payload.get('site') or {},
        },
    )
    return site


def ingest_site_created(topic, payload):
    if payload.get('schema') != 'scms.site.created.v1':
        raise ValidationError('Unsupported Site-created schema.')
    event_id = _event_id(payload)
    existing = InboxEvent.objects.filter(
        event_id=event_id, status=InboxEvent.PROCESSED,
    ).first()
    if existing:
        return SiteProjection.objects.get(source_id=_identity(payload)['site_id'])
    try:
        with transaction.atomic():
            site = _project_identity(payload)
            site.projection_status = SiteProjection.DRAFT
            site.last_error = ''
            site.save(update_fields=['projection_status', 'last_error', 'updated_at'])
            _record_inbox(event_id, topic, payload, InboxEvent.PROCESSED)
            return site
    except Exception as exc:
        _record_inbox(event_id, topic, payload, InboxEvent.FAILED, str(exc))
        raise


def fetch_execution_snapshot(snapshot_path):
    base_url = settings.MINITOS_URL.rstrip('/') + '/'
    url = urljoin(base_url, snapshot_path.lstrip('/'))
    headers = {'Accept': 'application/json'}
    if settings.MINITOS_OES_TOKEN:
        headers['X-OES-Token'] = settings.MINITOS_OES_TOKEN
    request = Request(url, headers=headers)
    with urlopen(request, timeout=settings.MINITOS_HTTP_TIMEOUT_SECONDS) as response:
        body = json.loads(response.read().decode('utf-8'))
    if not isinstance(body, dict):
        raise ValidationError('MiniTOS returned an invalid execution snapshot document.')
    return body


def _snapshot_checksum(payload):
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
        allow_nan=False,
    ).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _validate_snapshot(document, identity, data):
    snapshot = document.get('payload')
    if not isinstance(snapshot, dict):
        raise ValidationError('Execution snapshot response is missing payload.')
    expected_revision = int(data['configuration_revision'])
    if int(document.get('revision', -1)) != expected_revision:
        raise ValidationError('Execution snapshot revision does not match the event.')
    if str(document.get('checksum', '')) != str(data['snapshot_checksum']):
        raise ValidationError('Execution snapshot checksum does not match the event.')
    if _snapshot_checksum(snapshot) != data['snapshot_checksum']:
        raise ValidationError('Execution snapshot payload failed checksum validation.')
    snapshot_site = snapshot.get('site') or {}
    if str(snapshot_site.get('id')) != str(identity['site_id']):
        raise ValidationError('Execution snapshot belongs to another Site.')
    if int(snapshot_site.get('configuration_revision', -1)) != expected_revision:
        raise ValidationError('Execution snapshot contains the wrong Site revision.')
    return snapshot


def _import_equipment(site, snapshot):
    equipment = snapshot.get('equipment') or {}
    type_codes = {
        int(item['id']): item.get('equipment_type', '')
        for item in equipment.get('types', [])
    }
    now = timezone.now()
    for che in equipment.get('ches', []):
        che_id = int(che['id'])
        equipment_type_id = che.get('equipment_type_id')
        EquipmentState.objects.update_or_create(
            site=site,
            tos_che_id=che_id,
            defaults={
                'name': che['name'],
                'equipment_type_id': equipment_type_id,
                'equipment_type_code': type_codes.get(equipment_type_id, ''),
                'powered_on': False,
                'working': False,
                'is_available': False,
                'is_dispatched': False,
                'is_in_maintenance': bool(che.get('is_in_maintenance', False)),
                'latitude': che.get('latitude'),
                'longitude': che.get('longitude'),
                'rotation': float(che.get('rotation', 0.0)),
                'state_source': EquipmentState.IMPORT,
                'source_timestamp': None,
                'last_seen_at': now,
                'raw_state': {
                    'configuration_revision': site.source_revision,
                    'master_data': che,
                },
            },
        )


def ingest_site_configuration_published(topic, payload, *, snapshot_loader=None):
    if payload.get('schema') != 'scms.site.configuration.published.v1':
        raise ValidationError('Unsupported Site-configuration schema.')
    event_id = _event_id(payload)
    existing = InboxEvent.objects.filter(
        event_id=event_id, status=InboxEvent.PROCESSED,
    ).first()
    if existing:
        return SiteProjection.objects.get(source_id=_identity(payload)['site_id'])

    identity = _identity(payload)
    data = payload.get('data') or {}
    try:
        revision = int(data['configuration_revision'])
        snapshot_path = str(data['snapshot_path'])
        expected_checksum = str(data['snapshot_checksum'])
    except (KeyError, TypeError, ValueError) as exc:
        _record_inbox(event_id, topic, payload, InboxEvent.FAILED, str(exc))
        raise ValidationError(
            'Configuration event requires revision, snapshot path, and checksum.'
        ) from exc

    try:
        with transaction.atomic():
            site = _project_identity(payload)
            if revision <= site.source_revision:
                _record_inbox(event_id, topic, payload, InboxEvent.PROCESSED)
                return site
            site.projection_status = SiteProjection.SYNCING
            site.last_error = ''
            site.save(update_fields=['projection_status', 'last_error', 'updated_at'])

        loader = snapshot_loader or fetch_execution_snapshot
        document = loader(snapshot_path)
        snapshot = _validate_snapshot(document, identity, data)

        with transaction.atomic():
            site = SiteProjection.objects.select_for_update().get(source_id=identity['site_id'])
            if revision <= site.source_revision:
                _record_inbox(event_id, topic, payload, InboxEvent.PROCESSED)
                return site
            site.lifecycle_status = 'published'
            site.projection_status = SiteProjection.READY
            site.source_revision = revision
            site.snapshot_schema = str(data.get('snapshot_schema', snapshot.get('schema', '')))
            site.snapshot_checksum = expected_checksum
            site.snapshot_path = snapshot_path
            site.snapshot_payload = snapshot
            site.last_reconciled_at = timezone.now()
            site.last_error = ''
            site.save()
            _import_equipment(site, snapshot)
            _record_inbox(event_id, topic, payload, InboxEvent.PROCESSED)
            return site
    except Exception as exc:
        SiteProjection.objects.filter(source_id=identity['site_id']).update(
            projection_status=SiteProjection.ERROR,
            last_error=str(exc),
        )
        _record_inbox(event_id, topic, payload, InboxEvent.FAILED, str(exc))
        raise
