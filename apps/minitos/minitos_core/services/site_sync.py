import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from minitos_core import models as domain


DEFAULT_ENTERPRISE_CODE = 'terminal-velocity'
DEFAULT_ENTERPRISE_NAME = 'Terminal Velocity'
SNAPSHOT_SCHEMA = 'scms.site.execution-snapshot.v1'


def _topic_root(site):
    root = getattr(settings, 'SITE_UNS_ROOT', 'scms/v2').strip('/')
    return f'{root}/{site.enterprise.code}/{site.code}'


def _event_payload(site, schema, event_id, data):
    return {
        'schema': schema,
        'event_id': str(event_id),
        'occurred_at': timezone.now().isoformat(),
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
            'location_type': site.location.location_type,
        },
        'data': data,
    }


def _available_site_code(enterprise, location):
    base = slugify(location.name)[:70] or f'site-{location.pk}'
    candidate = base
    suffix = 2
    while domain.Site.objects.filter(enterprise=enterprise, code=candidate).exists():
        candidate = f'{base[:74]}-{suffix}'
        suffix += 1
    return candidate


def _emit_site_created(site):
    event_id = uuid.uuid4()
    schema = 'scms.site.created.v1'
    domain.IntegrationOutbox.objects.create(
        event_id=event_id,
        topic=f'{_topic_root(site)}/tos/site/created',
        schema=schema,
        payload=_event_payload(
            site,
            schema,
            event_id,
            {
                'lifecycle_status': site.lifecycle_status,
                'configuration_revision': site.configuration_revision,
                'simulation_enabled': site.simulation_enabled,
            },
        ),
    )


@transaction.atomic
def create_site(*, enterprise, location, code=None, simulation_enabled=True):
    if location.location_type not in {domain.Location.TL, domain.Location.ID}:
        raise ValidationError('Only terminal and inland-depot Locations can become Sites.')
    site = domain.Site.objects.create(
        enterprise=enterprise,
        location=location,
        code=code or _available_site_code(enterprise, location),
        simulation_enabled=simulation_enabled,
    )
    _emit_site_created(site)
    return site


@transaction.atomic
def ensure_site_for_location(location, *, emit_event=True):
    if location.location_type not in {domain.Location.TL, domain.Location.ID}:
        raise ValidationError('Only terminal and inland-depot Locations can become Sites.')
    enterprise, _ = domain.Enterprise.objects.get_or_create(
        code=DEFAULT_ENTERPRISE_CODE,
        defaults={'name': DEFAULT_ENTERPRISE_NAME},
    )
    site = domain.Site.objects.filter(location=location).first()
    if site:
        return site, False
    site = domain.Site.objects.create(
        enterprise=enterprise, location=location, code=_available_site_code(enterprise, location),
    )
    if emit_event:
        _emit_site_created(site)
    return site, True


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _rows(queryset):
    result = []
    for instance in queryset:
        row = {}
        for field in instance._meta.concrete_fields:
            row[field.attname] = _json_value(getattr(instance, field.attname))
        result.append(row)
    return result


def _canonical_json(payload):
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
        allow_nan=False,
    ).encode('utf-8')


def snapshot_checksum(payload):
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def build_execution_snapshot(site, layout, revision):
    assets = domain.LayoutAsset.objects.filter(layout_version=layout).order_by('pk')
    axes = domain.LinearAxis.objects.filter(asset__layout_version=layout).order_by('pk')
    interfaces = domain.TransferInterface.objects.filter(
        asset__layout_version=layout,
    ).order_by('pk')
    grids = domain.StorageGrid.objects.filter(
        layout_asset__layout_version=layout,
    ).order_by('pk')
    route_edges = domain.RouteEdge.objects.filter(layout_version=layout).order_by('pk')
    equipment = domain.Che.objects.filter(location=site.location).order_by('pk')
    equipment_type_ids = equipment.values_list('equipment_type_id', flat=True).distinct()
    equipment_types = domain.EquipmentType.objects.filter(pk__in=equipment_type_ids).order_by('pk')
    components = domain.CheComponent.objects.filter(che__location=site.location).order_by('pk')
    component_type_ids = set(components.values_list('component_type_id', flat=True))
    component_type_ids.update(
        domain.EquipmentTypeComponent.objects.filter(
            equipment_type_id__in=equipment_type_ids,
        ).values_list('component_type_id', flat=True)
    )
    policies = domain.AccessPolicy.objects.filter(layout_version=layout).order_by('pk')
    policy_rows = _rows(policies)
    policy_types = {
        policy.pk: list(policy.allowed_che_types.order_by('pk').values_list('pk', flat=True))
        for policy in policies
    }
    for row in policy_rows:
        row['allowed_che_type_ids'] = policy_types[row['id']]

    return {
        'schema': SNAPSHOT_SCHEMA,
        'generated_at': timezone.now().isoformat(),
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
            'lifecycle_status': domain.Site.PUBLISHED,
            'configuration_revision': revision,
            'simulation_enabled': site.simulation_enabled,
        },
        'location': _rows(domain.Location.objects.filter(pk=site.location_id))[0],
        'layout': {
            'version': _rows(domain.LayoutVersion.objects.filter(pk=layout.pk))[0],
            'access_policies': policy_rows,
            'assets': _rows(assets),
            'linear_axes': _rows(axes),
            'linear_markers': _rows(
                domain.LinearMarker.objects.filter(axis__asset__layout_version=layout).order_by('pk')
            ),
            'berth_segments': _rows(
                domain.BerthSegment.objects.filter(quay_asset__layout_version=layout).order_by('pk')
            ),
            'storage_grids': _rows(grids),
            'piles': _rows(
                domain.Pile.objects.filter(storage_grid__layout_asset__layout_version=layout).order_by('pk')
            ),
            'positions': _rows(
                domain.Position.objects.filter(
                    pile__storage_grid__layout_asset__layout_version=layout,
                ).order_by('pk')
            ),
            'transfer_interfaces': _rows(interfaces),
            'interface_positions': _rows(
                domain.InterfacePosition.objects.filter(
                    transfer_interface__asset__layout_version=layout,
                ).order_by('pk')
            ),
            'operational_positions': _rows(
                domain.OperationalPosition.objects.filter(layout_version=layout).order_by('pk')
            ),
            'route_nodes': _rows(
                domain.RouteNode.objects.filter(layout_version=layout).order_by('pk')
            ),
            'route_edges': _rows(route_edges),
            'route_edge_requirements': _rows(
                domain.RouteEdgeRequirement.objects.filter(
                    route_edge__layout_version=layout,
                ).order_by('pk')
            ),
        },
        'equipment': {
            'types': _rows(equipment_types),
            'component_types': _rows(
                domain.ComponentType.objects.filter(pk__in=component_type_ids).order_by('pk')
            ),
            'type_components': _rows(
                domain.EquipmentTypeComponent.objects.filter(
                    equipment_type_id__in=equipment_type_ids,
                ).order_by('pk')
            ),
            'ches': _rows(equipment),
            'operating_scopes': _rows(
                domain.CheOperatingScope.objects.filter(
                    Q(che__location=site.location) | Q(equipment_type_id__in=equipment_type_ids)
                ).order_by('pk')
            ),
            'components': _rows(components),
            'engines': _rows(domain.Engine.objects.filter(component__che__location=site.location).order_by('pk')),
            'batteries': _rows(domain.Battery.objects.filter(component__che__location=site.location).order_by('pk')),
            'energy_tanks': _rows(
                domain.EnergyTank.objects.filter(component__che__location=site.location).order_by('pk')
            ),
            'motion_axes': _rows(
                domain.MotionAxis.objects.filter(component__che__location=site.location).order_by('pk')
            ),
            'spreaders': _rows(domain.Spreader.objects.filter(component__che__location=site.location).order_by('pk')),
            'signal_bindings': _rows(
                domain.SignalBinding.objects.filter(component__che__location=site.location).order_by('pk')
            ),
        },
    }


@transaction.atomic
def publish_site_configuration(layout):
    site, _ = ensure_site_for_location(layout.location)
    site = domain.Site.objects.select_for_update().select_related(
        'enterprise', 'location',
    ).get(pk=site.pk)
    revision = site.configuration_revision + 1
    payload = build_execution_snapshot(site, layout, revision)
    checksum = snapshot_checksum(payload)
    snapshot = domain.SiteConfigurationSnapshot.objects.create(
        site=site,
        revision=revision,
        layout_version=layout,
        schema=SNAPSHOT_SCHEMA,
        checksum=checksum,
        payload=payload,
    )
    site.lifecycle_status = domain.Site.PUBLISHED
    site.configuration_revision = revision
    site.published_at = timezone.now()
    site.save(update_fields=[
        'lifecycle_status', 'configuration_revision', 'published_at', 'updated_at',
    ])

    event_id = uuid.uuid4()
    schema = 'scms.site.configuration.published.v1'
    snapshot_path = (
        f'/minitos_core/sites/{site.pk}/execution-snapshot/?revision={revision}'
    )
    domain.IntegrationOutbox.objects.create(
        event_id=event_id,
        topic=f'{_topic_root(site)}/tos/configuration/published',
        schema=schema,
        payload=_event_payload(
            site,
            schema,
            event_id,
            {
                'configuration_revision': revision,
                'layout_version_id': layout.pk,
                'lifecycle_status': domain.Site.PUBLISHED,
                'simulation_enabled': site.simulation_enabled,
                'snapshot_schema': snapshot.schema,
                'snapshot_checksum': checksum,
                'snapshot_path': snapshot_path,
            },
        ),
    )
    return snapshot
