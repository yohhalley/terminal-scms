from django.db import migrations, models
import django.db.models.deletion


def geometry_center(geometry):
    geometry = geometry or {}
    coordinates = geometry.get('coordinates', [])
    if geometry.get('type') == 'Point' and len(coordinates) >= 2:
        return [float(coordinates[0]), float(coordinates[1])]
    if geometry.get('type') == 'LineString' and coordinates:
        return [
            sum(float(point[0]) for point in coordinates) / len(coordinates),
            sum(float(point[1]) for point in coordinates) / len(coordinates),
        ]
    if geometry.get('type') == 'Polygon' and coordinates and coordinates[0]:
        ring = coordinates[0][:-1] if len(coordinates[0]) > 1 and coordinates[0][0] == coordinates[0][-1] else coordinates[0]
        return [
            sum(float(point[0]) for point in ring) / len(ring),
            sum(float(point[1]) for point in ring) / len(ring),
        ]
    return None


def backfill_interface_hubs(apps, schema_editor):
    TransferInterface = apps.get_model('minitos_core', 'TransferInterface')
    RouteNode = apps.get_model('minitos_core', 'RouteNode')
    RouteEdge = apps.get_model('minitos_core', 'RouteEdge')
    database = schema_editor.connection.alias
    modes = {
        'yard_block': 'crane', 'quay': 'crane', 'rail_area': 'crane',
        'road': 'road', 'parking': 'road', 'gate_area': 'road',
    }
    for interface in TransferInterface.objects.using(database).select_related('asset').all():
        asset = interface.asset
        asset_point = geometry_center(asset.geometry)
        positions = list(interface.positions.using(database).all())
        interface_point = (
            [
                sum(float(position.longitude) for position in positions) / len(positions),
                sum(float(position.latitude) for position in positions) / len(positions),
            ]
            if positions else geometry_center(interface.geometry or asset.geometry)
        )
        if not asset_point or not interface_point:
            continue
        asset_node, _ = RouteNode.objects.using(database).update_or_create(
            layout_version_id=asset.layout_version_id,
            code=f'A{asset.pk}.NODE',
            defaults={
                'asset_id': asset.pk,
                'name': f'{asset.code} area',
                'node_kind': 'operational',
                'point': {'type': 'Point', 'coordinates': asset_point},
                'is_generated': True,
                'is_enabled': asset.is_active,
                'metadata': {'aggregate_kind': 'asset', 'asset_id': asset.pk},
            },
        )
        interface_node, _ = RouteNode.objects.using(database).update_or_create(
            transfer_interface_id=interface.pk,
            defaults={
                'layout_version_id': asset.layout_version_id,
                'code': f'A{asset.pk}.IF{interface.pk}.NODE',
                'asset_id': asset.pk,
                'name': f'{interface.code} interface',
                'node_kind': 'interface',
                'point': {'type': 'Point', 'coordinates': interface_point},
                'is_generated': True,
                'is_enabled': interface.is_enabled,
                'metadata': {
                    'aggregate_kind': 'interface',
                    'asset_id': asset.pk,
                    'interface_id': interface.pk,
                },
            },
        )
        mode = modes.get(asset.asset_kind, 'static')
        for suffix, from_node, to_node in [('OUT', asset_node, interface_node), ('IN', interface_node, asset_node)]:
            RouteEdge.objects.using(database).update_or_create(
                layout_version_id=asset.layout_version_id,
                code=f'AUTO.A{asset.pk}.IF{interface.pk}.{suffix}',
                defaults={
                    'name': f'{asset.code} to {interface.code}',
                    'from_node_id': from_node.pk,
                    'to_node_id': to_node.pk,
                    'edge_kind': 'handle',
                    'movement_mode': mode,
                    'access_policy_id': interface.access_policy_id or asset.access_policy_id,
                    'distance_m': 0,
                    'capacity': interface.capacity,
                    'base_cost': 0.01,
                    'is_generated': True,
                    'is_enabled': interface.is_enabled and asset.is_active,
                    'metadata': {'connector_kind': 'asset_interface', 'interface_id': interface.pk},
                },
            )
        position_nodes = RouteNode.objects.using(database).filter(
            operational_position__interface_position__transfer_interface_id=interface.pk,
        ).select_related('operational_position__interface_position')
        for position_node in position_nodes:
            metadata = dict(position_node.metadata or {})
            metadata.update(aggregate_kind='interface_position', interface_id=interface.pk)
            position_node.node_kind = 'operational'
            position_node.metadata = metadata
            position_node.save(update_fields=['node_kind', 'metadata'])
            position_id = position_node.operational_position.interface_position_id
            for suffix, from_node, to_node in [('OUT', interface_node, position_node), ('IN', position_node, interface_node)]:
                RouteEdge.objects.using(database).update_or_create(
                    layout_version_id=asset.layout_version_id,
                    code=f'AUTO.IF{interface.pk}.P{position_id}.{suffix}',
                    defaults={
                        'name': f'{interface.code} position access',
                        'from_node_id': from_node.pk,
                        'to_node_id': to_node.pk,
                        'edge_kind': 'handoff',
                        'movement_mode': 'static',
                        'access_policy_id': interface.access_policy_id,
                        'distance_m': 0,
                        'capacity': position_node.operational_position.interface_position.capacity,
                        'base_cost': 0.01,
                        'is_generated': True,
                        'is_enabled': interface.is_enabled,
                        'metadata': {
                            'connector_kind': 'interface_position',
                            'interface_id': interface.pk,
                            'interface_position_id': position_id,
                        },
                    },
                )


def remove_interface_hubs(apps, schema_editor):
    RouteNode = apps.get_model('minitos_core', 'RouteNode')
    RouteEdge = apps.get_model('minitos_core', 'RouteEdge')
    database = schema_editor.connection.alias
    RouteEdge.objects.using(database).filter(metadata__connector_kind__in=[
        'asset_interface', 'interface_position', 'interface_road', 'road_junction',
    ]).delete()
    RouteNode.objects.using(database).filter(metadata__aggregate_kind__in=['asset', 'interface']).delete()
    RouteNode.objects.using(database).filter(metadata__aggregate_kind='interface_position').update(
        node_kind='interface', metadata={},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('minitos_core', '0038_interfaceposition_lane_index_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='transferinterface',
            name='road_axis',
            field=models.ForeignKey(
                blank=True,
                help_text='Adjacent road centerline used to enter or leave this interface.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='road_interfaces',
                to='minitos_core.linearaxis',
            ),
        ),
        migrations.AddField(
            model_name='routenode',
            name='transfer_interface',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='route_node',
                to='minitos_core.transferinterface',
            ),
        ),
        migrations.RunPython(backfill_interface_hubs, remove_interface_hubs),
    ]
