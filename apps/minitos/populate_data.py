#!/usr/bin/env python3
"""Create an editable sample terminal using the versioned layout/routing schema.

Run with: python manage.py shell < populate_data.py
"""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings')
django.setup()

from minitos_core.models import (  # noqa: E402
    AccessPolicy, BerthSegment, Che, CheOperatingScope, EquipmentType,
    LayoutAsset, LinearAxis, Location, PointOfWork, RouteEdge, RouteNode,
    StorageGrid, TransferInterface,
)
from minitos_core.services import (  # noqa: E402
    current_layout, geographic_distance_m, local_to_geographic,
    sync_axis_graph, sync_berth_bollards, sync_interface_positions,
    sync_yard_grid,
)


location, _ = Location.objects.get_or_create(
    name='Test Terminal',
    defaults={
        'location_type': Location.TL,
        'latitude': 17.954711,
        'longitude': -102.162543,
        'map_origin_latitude': 17.954711,
        'map_origin_longitude': -102.162543,
        'map_width_m': 900,
        'map_height_m': 650,
        'timezone': 'America/Mexico_City',
    },
)
if location.map_origin_latitude is None or location.map_origin_longitude is None:
    location.map_origin_latitude = location.latitude
    location.map_origin_longitude = location.longitude
    location.save(update_fields=['map_origin_latitude', 'map_origin_longitude'])

layout = current_layout(location)


def coordinate(x_m, y_m):
    latitude, longitude = local_to_geographic(location, x_m, y_m)
    return [longitude, latitude]


def rectangle(x_m, y_m, width_m, height_m):
    points = [
        coordinate(x_m, y_m),
        coordinate(x_m + width_m, y_m),
        coordinate(x_m + width_m, y_m + height_m),
        coordinate(x_m, y_m + height_m),
    ]
    return {'type': 'Polygon', 'coordinates': [[*points, points[0]]]}


def asset(code, name, kind, geometry, parent=None, policy=None):
    value, _ = LayoutAsset.objects.update_or_create(
        layout_version=layout,
        code=code,
        defaults={
            'name': name,
            'asset_kind': kind,
            'geometry': geometry,
            'parent': parent,
            'access_policy': policy,
            'is_visible': True,
            'is_active': True,
        },
    )
    return value


equipment = {}
for code, name, flags, dimensions in [
    ('TT', 'Terminal Truck', {'is_horizontal_transport': True}, (7.0, 2.6, 3.2)),
    ('RTG', 'Rubber Tyred Gantry', {'is_yard_equipment': True}, (26.0, 11.0, 20.0)),
    ('STS', 'Ship-to-Shore Crane', {'is_quay_equipment': True}, (30.0, 27.0, 70.0)),
]:
    equipment[code], _ = EquipmentType.objects.update_or_create(
        name=name,
        defaults={
            'equipment_type': code,
            'length_m': dimensions[0],
            'width_m': dimensions[1],
            'height_m': dimensions[2],
            **flags,
        },
    )

tt_policy, _ = AccessPolicy.objects.update_or_create(
    layout_version=layout,
    code='TT-ROAD',
    defaults={'name': 'Terminal trucks on roads', 'allow_loaded': True, 'allow_empty': True},
)
tt_policy.allowed_che_types.set([equipment['TT']])

terminal = asset('TERMINAL', 'Terminal boundary', LayoutAsset.TERMINAL, rectangle(0, 0, 900, 650))
land = asset('LAND', 'Operational land', LayoutAsset.LAND, rectangle(0, 0, 900, 560), terminal)
asset('WATER', 'Harbour water', LayoutAsset.WATER, rectangle(0, 560, 900, 90), terminal)

road = asset('ROAD-01', 'Main road spine', LayoutAsset.ROAD, rectangle(60, 250, 780, 35), land, tt_policy)
road_points = [coordinate(70, 267), coordinate(300, 267), coordinate(580, 267), coordinate(830, 267)]
road_axis, _ = LinearAxis.objects.update_or_create(
    asset=road,
    code='ROAD-01.CENTER',
    defaults={
        'name': 'Main road centerline',
        'axis_kind': LinearAxis.ROAD_CENTERLINE,
        'points': road_points,
        'direction': LinearAxis.BOTH,
        'lane_count': 2,
        'capacity': 8,
        'width_m': 14,
        'speed_limit_kph': 30,
        'access_policy': tt_policy,
    },
)
sync_axis_graph(road_axis)

blocks = []
for index, (code, x_m, y_m) in enumerate([
    ('A01', 120, 330), ('A02', 390, 330), ('A03', 660, 330),
], start=1):
    block = asset(code, f'Yard block {code}', LayoutAsset.YARD_BLOCK, rectangle(x_m, y_m, 150, 150), land)
    grid, _ = StorageGrid.objects.update_or_create(
        layout_asset=block,
        defaults={
            'name': code,
            'grid_kind': StorageGrid.MANUAL,
            'max_bays': 20,
            'max_rows': 6,
            'max_tier': 5,
            'longitude': coordinate(x_m, y_m)[0],
            'latitude': coordinate(x_m, y_m)[1],
            'heading_deg': 0,
            'bay_use_heading_axis': True,
            'bay_naming': StorageGrid.ODD_NUMBERS,
            'row_naming': StorageGrid.LETTERS,
            'bay_spacing_m': 0.4,
            'row_spacing_m': 0.3,
        },
    )
    sync_yard_grid(grid)
    interface, _ = TransferInterface.objects.update_or_create(
        asset=block,
        code=f'{code}.TL',
        defaults={
            'name': f'{code} truck transfer lane',
            'storage_grid': grid,
            'axis': road_axis,
            'access_policy': tt_policy,
            'interface_kind': TransferInterface.VEHICLE_LANE,
            'side': TransferInterface.LEFT,
            'geometry': {
                'type': 'LineString',
                'coordinates': [coordinate(x_m, y_m - 12), coordinate(x_m + 150, y_m - 12)],
            },
            'capacity': 4,
            'requires_vehicle_present': True,
        },
    )
    sync_interface_positions(interface, 4)
    blocks.append((block, grid, interface))

quay = asset('QUAY-01', 'Main quay', LayoutAsset.QUAY, rectangle(100, 525, 700, 35), land)
quay_points = [coordinate(100, 560), coordinate(800, 560)]
quay_axis, _ = LinearAxis.objects.update_or_create(
    asset=quay,
    code='QUAY-01.FACE',
    defaults={
        'name': 'Main quay face',
        'axis_kind': LinearAxis.QUAY_FACE,
        'points': quay_points,
        'direction': LinearAxis.BOTH,
        'lane_count': 1,
        'capacity': 3,
    },
)
sync_axis_graph(quay_axis)
berth_length = geographic_distance_m(quay_points[0], quay_points[1])
berth, _ = BerthSegment.objects.update_or_create(
    quay_asset=quay,
    code='BERTH-01',
    defaults={
        'quay_face_axis': quay_axis,
        'name': 'Main berth',
        'start_chainage_m': 0,
        'end_chainage_m': berth_length,
        'water_side': BerthSegment.WATER_LEFT,
        'bollard_direction': BerthSegment.BOLLARDS_FORWARD,
    },
)
sync_berth_bollards(berth, bollard_count=25, default_spacing_m=25)

road_nodes = list(RouteNode.objects.filter(metadata__axis_id=road_axis.pk).order_by('code'))
for block, _, interface in blocks:
    interface_node = RouteNode.objects.filter(
        operational_position__interface_position__transfer_interface=interface,
    ).order_by('code').first()
    road_node = min(
        road_nodes,
        key=lambda node: geographic_distance_m(
            node.point['coordinates'], interface_node.point['coordinates'],
        ),
    )
    for suffix, start, end in [('IN', road_node, interface_node), ('OUT', interface_node, road_node)]:
        RouteEdge.objects.update_or_create(
            layout_version=layout,
            code=f'{interface.code}.{suffix}',
            defaults={
                'name': f'{block.code} transfer {suffix.lower()}',
                'from_node': start,
                'to_node': end,
                'edge_kind': RouteEdge.HANDOFF,
                'movement_mode': RouteEdge.ROAD,
                'access_policy': tt_policy,
                'distance_m': geographic_distance_m(start.point['coordinates'], end.point['coordinates']),
                'capacity': interface.capacity,
                'base_cost': 1,
            },
        )

for index, (code, equipment_code, x_m, y_m, current_asset) in enumerate([
    ('TT01', 'TT', 200, 267, road),
    ('RTG01', 'RTG', 190, 400, blocks[0][0]),
    ('STS01', 'STS', 300, 550, quay),
], start=1):
    longitude, latitude = coordinate(x_m, y_m)
    che, _ = Che.objects.update_or_create(
        name=code,
        defaults={
            'equipment_type': equipment[equipment_code],
            'location': location,
            'current_asset': current_asset,
            'longitude': longitude,
            'latitude': latitude,
            'powered_on': False,
            'working': False,
            'is_available': False,
        },
    )
    if equipment_code == 'STS':
        CheOperatingScope.objects.update_or_create(
            che=che,
            axis=quay_axis,
            defaults={
                'asset': quay,
                'min_chainage_m': 50,
                'max_chainage_m': berth_length - 50,
                'can_reposition': False,
            },
        )
    elif equipment_code == 'RTG':
        CheOperatingScope.objects.update_or_create(
            che=che,
            asset=current_asset,
            axis=None,
            defaults={'bay_start': 1, 'bay_end': 20, 'row_start': 1, 'row_end': 6},
        )
    else:
        CheOperatingScope.objects.update_or_create(
            che=che,
            asset=road,
            axis=road_axis,
            defaults={'can_reposition': True},
        )

first_transfer = blocks[0][2].positions.order_by('sequence').first()
PointOfWork.objects.update_or_create(
    location=location,
    code='POW-A01',
    defaults={
        'name': 'A01 transfer work point',
        'pow_type': PointOfWork.TERMINAL_AREA,
        'latitude': first_transfer.latitude,
        'longitude': first_transfer.longitude,
        'operational_position': first_transfer.operational_position,
        'route_node': first_transfer.operational_position.route_node,
        'max_throughput': 40,
    },
)

print(
    f'Created {location.name} layout v{layout.version}: '
    f'{layout.assets.count()} assets, {layout.route_nodes.count()} nodes, '
    f'{layout.route_edges.count()} edges.'
)
