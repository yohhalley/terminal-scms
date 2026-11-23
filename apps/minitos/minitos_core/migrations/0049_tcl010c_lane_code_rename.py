# TCL-010c: rename the import_v1 hand-off lanes to the codes agreed in the
# TCL-011 hand-off design (SME finding 7): QC01.LANE -> QC01-L1 and
# BLOCK01.TL -> BLOCK01-TL.
#
# The lanes only exist in databases seeded by the import_v1 scenario; a
# scenario reset re-seeds them with the new codes anyway. This migration keeps
# a database that has not been reset consistent with the seed: it renames the
# transfer interface and every generated row whose code or name is derived
# from the lane code (interface positions ``<lane>.NN``, operational positions
# and their ``logical_address['interface']``, route nodes ``<lane>.NN.NODE``,
# hand-off edges ``<lane>.NN.IN/OUT`` and generated interface edges/nodes that
# carry the lane code in their name). Rows are matched by primary key through
# the interface, so other assets are not touched. An interface is skipped if
# its asset already has a lane with the target code. Row ids do not change, so
# orders, legs and routes keep pointing at the same positions. No-op on a
# database without these lanes.

from django.db import migrations

RENAMES = (
    ('QC01.LANE', 'QC01-L1'),
    ('BLOCK01.TL', 'BLOCK01-TL'),
)


def _swap(value, old, new):
    if value == old:
        return new
    if value.startswith(f'{old}.'):
        return new + value[len(old):]
    return value


def _rename(apps, pairs):
    TransferInterface = apps.get_model('minitos_core', 'TransferInterface')
    InterfacePosition = apps.get_model('minitos_core', 'InterfacePosition')
    OperationalPosition = apps.get_model('minitos_core', 'OperationalPosition')
    RouteNode = apps.get_model('minitos_core', 'RouteNode')
    RouteEdge = apps.get_model('minitos_core', 'RouteEdge')

    for old, new in pairs:
        for interface in TransferInterface.objects.filter(code=old):
            if TransferInterface.objects.filter(asset_id=interface.asset_id, code=new).exists():
                continue
            interface.code = new
            interface.save(update_fields=['code'])

            positions = InterfacePosition.objects.filter(transfer_interface=interface)
            for position in positions:
                position.code = _swap(position.code, old, new)
                position.save(update_fields=['code'])

            operational = OperationalPosition.objects.filter(interface_position__transfer_interface=interface)
            for row in operational:
                row.code = _swap(row.code, old, new)
                address = dict(row.logical_address or {})
                if address.get('interface') == old:
                    address['interface'] = new
                row.logical_address = address
                row.save(update_fields=['code', 'logical_address'])

            nodes = RouteNode.objects.filter(operational_position__interface_position__transfer_interface=interface)
            interface_nodes = RouteNode.objects.filter(metadata__interface_id=interface.pk)
            node_ids = set(nodes.values_list('pk', flat=True)) | set(interface_nodes.values_list('pk', flat=True))
            for node in RouteNode.objects.filter(pk__in=node_ids):
                node.code = _swap(node.code, old, new)
                node.name = node.name.replace(old, new)
                node.save(update_fields=['code', 'name'])

            edges = (
                RouteEdge.objects.filter(from_node_id__in=node_ids)
                | RouteEdge.objects.filter(to_node_id__in=node_ids)
                | RouteEdge.objects.filter(metadata__interface_id=interface.pk)
            )
            for edge in edges.distinct():
                edge.code = _swap(edge.code, old, new)
                edge.name = edge.name.replace(old, new)
                edge.save(update_fields=['code', 'name'])


def forwards(apps, schema_editor):
    _rename(apps, RENAMES)


def backwards(apps, schema_editor):
    _rename(apps, tuple((new, old) for old, new in RENAMES))


class Migration(migrations.Migration):

    dependencies = [
        ('minitos_core', '0048_scenario_seed_singleton'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
