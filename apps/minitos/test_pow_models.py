#!/usr/bin/env python3
"""Demonstrate PointOfWork references into the versioned routing graph."""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'minitos_site.settings')
django.setup()

from minitos_core.models import (  # noqa: E402
    LayoutAsset, Location, OperationalPosition, PointOfWork, RouteEdge, RouteNode,
)
from minitos_core.services import current_layout, local_to_geographic  # noqa: E402


def test_point_of_work():
    location = Location.objects.first()
    if not location:
        print('No location found. Create a terminal first.')
        return

    layout = current_layout(location)
    asset, _ = LayoutAsset.objects.get_or_create(
        layout_version=layout,
        code='POW-NETWORK',
        defaults={
            'name': 'Point-of-work network',
            'asset_kind': LayoutAsset.ROAD,
            'geometry': {'type': 'LineString', 'coordinates': []},
        },
    )

    definitions = [
        ('EP01', 'Main entry gate', PointOfWork.ENTRY_POINT, 50.0, 150.0),
        ('TA01', 'Main terminal area', PointOfWork.TERMINAL_AREA, 100.0, 200.0),
        ('DP01', 'Main departure gate', PointOfWork.DEPARTURE_POINT, 150.0, 150.0),
    ]
    nodes = {}
    for code, name, kind, x_m, y_m in definitions:
        latitude, longitude = local_to_geographic(location, x_m, y_m)
        position, _ = OperationalPosition.objects.update_or_create(
            layout_version=layout,
            code=code,
            defaults={
                'name': name,
                'position_kind': OperationalPosition.WORK_POINT,
                'asset': asset,
                'latitude': latitude,
                'longitude': longitude,
                'logical_address': {'point_of_work': code},
            },
        )
        node, _ = RouteNode.objects.update_or_create(
            layout_version=layout,
            code=code,
            defaults={
                'name': name,
                'node_kind': RouteNode.OPERATIONAL,
                'asset': asset,
                'operational_position': position,
                'point': {'type': 'Point', 'coordinates': [longitude, latitude]},
            },
        )
        PointOfWork.objects.update_or_create(
            location=location,
            code=code,
            defaults={
                'name': name,
                'pow_type': kind,
                'latitude': latitude,
                'longitude': longitude,
                'operational_position': position,
                'route_node': node,
                'max_throughput': 200,
            },
        )
        nodes[code] = node

    for code, start, end, distance in [
        ('EP01-TA01', 'EP01', 'TA01', 500),
        ('TA01-DP01', 'TA01', 'DP01', 300),
    ]:
        RouteEdge.objects.update_or_create(
            layout_version=layout,
            code=code,
            defaults={
                'name': code,
                'from_node': nodes[start],
                'to_node': nodes[end],
                'edge_kind': RouteEdge.TRAVEL,
                'movement_mode': RouteEdge.ROAD,
                'distance_m': distance,
                'capacity': 1,
                'base_cost': 1,
            },
        )

    print(f'{location.name}: {len(nodes)} POW nodes, {layout.route_edges.count()} route edges')


if __name__ == '__main__':
    test_point_of_work()
