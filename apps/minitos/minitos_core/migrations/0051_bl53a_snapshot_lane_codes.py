# BL-53a: rename the TCL-010c lane codes inside published site snapshots.
#
# 0049 renames the live layout rows (QC01.LANE -> QC01-L1, BLOCK01.TL ->
# BLOCK01-TL) in a database that is migrated in place instead of reset. The
# published SiteConfigurationSnapshot payloads are copies of those rows, so
# they still carried the old codes. This migration applies the same rename,
# with the same row selection as 0049, to every snapshot payload that still
# has an old lane, and recomputes its checksum with the publisher's own
# ``site_sync.snapshot_checksum``. Revisions, ``generated_at`` and every
# other field are unchanged, so the rewritten payload is what the publisher
# would build from the renamed rows.
#
# A ``configuration.published`` outbox event for a rewritten snapshot that has
# not been delivered yet (``published_at`` null) gets the new checksum too, so
# OES still validates the snapshot when it fetches it. Delivered events are a
# record of what was sent and are left alone; an OES projection that already
# synced that revision keeps the old codes until the next publish (a
# ``make scenario-reset`` republishes).
#
# Reversible: backwards swaps the pairs back, which restores the original
# payload and therefore the original checksum. No-op without snapshots.
#
# Numbered 0051 because draft PR #54 claims 0050.

from django.db import migrations

from minitos_core.services.site_sync import snapshot_checksum

RENAMES = (
    ('QC01.LANE', 'QC01-L1'),
    ('BLOCK01.TL', 'BLOCK01-TL'),
)
PUBLISHED_SCHEMA = 'scms.site.configuration.published.v1'


def _swap(value, old, new):
    if value == old:
        return new
    if isinstance(value, str) and value.startswith(f'{old}.'):
        return new + value[len(old):]
    return value


def _replace(value, old, new):
    return value.replace(old, new) if isinstance(value, str) else value


def rename_payload_lanes(payload, pairs):
    """Apply 0049's lane rename to one snapshot payload in place; True if
    anything changed. Rows are picked through the interface exactly as 0049
    picks the live rows, so only rows derived from the lane change."""
    layout = (payload or {}).get('layout') or {}
    interfaces = layout.get('transfer_interfaces') or []
    interface_positions = layout.get('interface_positions') or []
    operational = layout.get('operational_positions') or []
    nodes = layout.get('route_nodes') or []
    edges = layout.get('route_edges') or []
    changed = False
    for old, new in pairs:
        for interface in interfaces:
            if interface.get('code') != old:
                continue
            if any(other.get('asset_id') == interface.get('asset_id') and other.get('code') == new
                   for other in interfaces):
                continue
            interface['code'] = new
            changed = True
            interface_id = interface.get('id')

            position_ids = set()
            for position in interface_positions:
                if position.get('transfer_interface_id') == interface_id:
                    position['code'] = _swap(position.get('code'), old, new)
                    position_ids.add(position.get('id'))

            operational_ids = set()
            for row in operational:
                if row.get('interface_position_id') in position_ids:
                    row['code'] = _swap(row.get('code'), old, new)
                    address = row.get('logical_address')
                    if isinstance(address, dict) and address.get('interface') == old:
                        address['interface'] = new
                    operational_ids.add(row.get('id'))

            node_ids = set()
            for node in nodes:
                metadata = node.get('metadata') if isinstance(node.get('metadata'), dict) else {}
                if (node.get('operational_position_id') in operational_ids
                        or metadata.get('interface_id') == interface_id):
                    node['code'] = _swap(node.get('code'), old, new)
                    node['name'] = _replace(node.get('name'), old, new)
                    node_ids.add(node.get('id'))

            for edge in edges:
                metadata = edge.get('metadata') if isinstance(edge.get('metadata'), dict) else {}
                if (edge.get('from_node_id') in node_ids or edge.get('to_node_id') in node_ids
                        or metadata.get('interface_id') == interface_id):
                    edge['code'] = _swap(edge.get('code'), old, new)
                    edge['name'] = _replace(edge.get('name'), old, new)
    return changed


def _rewrite(apps, pairs):
    Snapshot = apps.get_model('minitos_core', 'SiteConfigurationSnapshot')
    Outbox = apps.get_model('minitos_core', 'IntegrationOutbox')
    for snapshot in Snapshot.objects.order_by('pk'):
        payload = snapshot.payload
        if not rename_payload_lanes(payload, pairs):
            continue
        old_checksum = snapshot.checksum
        snapshot.payload = payload
        snapshot.checksum = snapshot_checksum(payload)
        snapshot.save(update_fields=['payload', 'checksum'])
        pending = Outbox.objects.filter(schema=PUBLISHED_SCHEMA, published_at__isnull=True)
        for event in pending:
            envelope = event.payload or {}
            data = envelope.get('data') or {}
            if (str((envelope.get('site') or {}).get('id')) == str(snapshot.site_id)
                    and data.get('configuration_revision') == snapshot.revision
                    and data.get('snapshot_checksum') == old_checksum):
                data['snapshot_checksum'] = snapshot.checksum
                event.payload = envelope
                event.save(update_fields=['payload'])


def forwards(apps, schema_editor):
    _rewrite(apps, RENAMES)


def backwards(apps, schema_editor):
    _rewrite(apps, tuple((new, old) for old, new in RENAMES))


class Migration(migrations.Migration):

    dependencies = [
        ('minitos_core', '0049_tcl010c_lane_code_rename'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
