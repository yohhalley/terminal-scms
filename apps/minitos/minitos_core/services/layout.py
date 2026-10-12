import math

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone

from minitos_core.models import (
    BerthSegment,
    InterfacePosition,
    LayoutAsset,
    LayoutVersion,
    LinearAxis,
    LinearMarker,
    OperationalPosition,
    StorageGrid,
    RouteEdge,
    RouteNode,
    TransferInterface,
)
from minitos_core.services.coordinates import EARTH_RADIUS_M, offset_geographic


def geographic_distance_m(first, second):
    lon1, lat1 = map(float, first[:2])
    lon2, lat2 = map(float, second[:2])
    lat1_rad, lat2_rad = math.radians(lat1), math.radians(lat2)
    dlat = lat2_rad - lat1_rad
    dlon = math.radians(lon2 - lon1)
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(value)))


def polyline_length_m(points):
    return sum(geographic_distance_m(points[index], points[index + 1]) for index in range(len(points) - 1))


def point_along_axis(points, chainage_m, lateral_offset_m=0.0):
    if len(points) < 2:
        raise ValidationError('An axis requires at least two points.')
    remaining = max(0.0, float(chainage_m))
    for index in range(len(points) - 1):
        start, end = points[index], points[index + 1]
        segment_length = geographic_distance_m(start, end)
        if remaining <= segment_length or index == len(points) - 2:
            ratio = 0.0 if segment_length == 0 else min(1.0, remaining / segment_length)
            longitude = float(start[0]) + (float(end[0]) - float(start[0])) * ratio
            latitude = float(start[1]) + (float(end[1]) - float(start[1])) * ratio
            if lateral_offset_m:
                mean_latitude = math.radians((float(start[1]) + float(end[1])) / 2)
                east = math.radians(float(end[0]) - float(start[0])) * EARTH_RADIUS_M * math.cos(mean_latitude)
                north = math.radians(float(end[1]) - float(start[1])) * EARTH_RADIUS_M
                length = math.hypot(east, north) or 1.0
                latitude, longitude = offset_geographic(
                    latitude,
                    longitude,
                    -north / length * float(lateral_offset_m),
                    east / length * float(lateral_offset_m),
                )
            return [longitude, latitude]
        remaining -= segment_length
    return list(map(float, points[-1][:2]))


@transaction.atomic
def current_layout(location, user=None):
    layout = location.layout_versions.filter(is_current=True).first()
    if layout:
        return layout
    version = (location.layout_versions.aggregate(value=Max('version'))['value'] or 0) + 1
    return LayoutVersion.objects.create(
        location=location,
        version=version,
        name='Working layout',
        created_by=user if getattr(user, 'is_authenticated', False) else None,
    )


def _axis_movement_mode(axis_kind):
    return {
        LinearAxis.ROAD_CENTERLINE: RouteEdge.ROAD,
        LinearAxis.RAIL_TRACK: RouteEdge.RAIL,
        LinearAxis.CRANE_RAIL: RouteEdge.CRANE,
        LinearAxis.QUAY_FACE: RouteEdge.CRANE,
        LinearAxis.TRANSFER_LANE: RouteEdge.ROAD,
        LinearAxis.FREE_DRIVE_GUIDE: RouteEdge.FREE_DRIVE,
    }[axis_kind]


def _geometry_center(geometry):
    geometry = geometry or {}
    coordinates = geometry.get('coordinates', [])
    if geometry.get('type') == 'Point' and len(coordinates) >= 2:
        return list(map(float, coordinates[:2]))
    if geometry.get('type') == 'LineString' and len(coordinates) >= 2:
        return point_along_axis(coordinates, polyline_length_m(coordinates) / 2)
    if geometry.get('type') == 'Polygon' and coordinates and coordinates[0]:
        ring = coordinates[0]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        if ring:
            return [
                sum(float(point[0]) for point in ring) / len(ring),
                sum(float(point[1]) for point in ring) / len(ring),
            ]
    return None


def geometry_bounds_center(geometry):
    """Return the WGS-84 center of a GeoJSON geometry's bounding box."""
    points = []

    def collect(value):
        if not isinstance(value, (list, tuple)):
            return
        if len(value) >= 2 and not isinstance(value[0], (list, tuple)):
            try:
                longitude, latitude = float(value[0]), float(value[1])
            except (TypeError, ValueError):
                raise ValidationError('Geometry coordinates must be numeric.')
            if not math.isfinite(longitude) or not math.isfinite(latitude):
                raise ValidationError('Geometry coordinates must be finite.')
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValidationError('Geometry coordinates must use valid WGS-84 values.')
            points.append((longitude, latitude))
            return
        for child in value:
            collect(child)

    collect((geometry or {}).get('coordinates', []))
    if not points:
        raise ValidationError('Geometry must contain at least one coordinate point.')
    longitudes, latitudes = zip(*points)
    return [
        (min(longitudes) + max(longitudes)) / 2,
        (min(latitudes) + max(latitudes)) / 2,
    ]


def _asset_interface_mode(asset):
    if asset.asset_kind in {
        LayoutAsset.YARD_BLOCK, LayoutAsset.QUAY, LayoutAsset.RAIL_AREA,
    }:
        return RouteEdge.CRANE
    if asset.asset_kind in {
        LayoutAsset.ROAD, LayoutAsset.PARKING, LayoutAsset.GATE_AREA,
    }:
        return RouteEdge.ROAD
    return RouteEdge.STATIC


def _sync_road_asset_geometry(axis):
    if axis.axis_kind != LinearAxis.ROAD_CENTERLINE or axis.asset.asset_kind != LayoutAsset.ROAD:
        return
    geometry = {'type': 'LineString', 'coordinates': axis.points}
    if axis.asset.geometry == geometry:
        return
    LayoutAsset.objects.filter(pk=axis.asset_id).update(geometry=geometry)
    axis.asset.geometry = geometry


def _propagate_moved_road_vertices(axis, previous_points, tolerance_m=0.75):
    """Move vertices on other roads that shared a junction before this update."""
    _sync_road_asset_geometry(axis)
    if axis.axis_kind != LinearAxis.ROAD_CENTERLINE:
        return []
    current_points = axis.points or []
    previous_points = previous_points or []
    if len(previous_points) != len(current_points):
        return []
    moves = [
        (old_point, new_point)
        for old_point, new_point in zip(previous_points, current_points)
        if geographic_distance_m(old_point, new_point) > 0.01
    ]
    if not moves:
        return []

    connected_axes = []
    candidates = LinearAxis.objects.filter(
        asset__layout_version=axis.layout_version,
        axis_kind=LinearAxis.ROAD_CENTERLINE,
    ).exclude(pk=axis.pk).select_related('asset')
    for candidate in candidates:
        points = [list(point) for point in candidate.points or []]
        changed = False
        for index, point in enumerate(points):
            for old_point, new_point in moves:
                if geographic_distance_m(point, old_point) <= tolerance_m:
                    points[index] = list(map(float, new_point[:2]))
                    changed = True
                    break
        if not changed:
            continue
        candidate.points = points
        candidate.save(update_fields=['points'])
        _sync_road_asset_geometry(candidate)
        connected_axes.append(candidate)
    return connected_axes


def _sync_gate_endpoint_position(axis, nodes):
    generated = OperationalPosition.objects.filter(
        axis=axis,
        logical_address__generated_by='gate_axis_endpoint',
    )
    role = (axis.metadata or {}).get('facility_role')
    if axis.asset.asset_kind != LayoutAsset.GATE_AREA or role not in {'gate_in', 'gate_out'}:
        RouteNode.objects.filter(operational_position__in=generated).update(
            operational_position=None,
            node_kind=RouteNode.AXIS_POINT,
        )
        generated.delete()
        return None

    suffix = 'ENTRY' if role == 'gate_in' else 'EXIT'
    point = list(map(float, axis.points[0][:2]))
    position, _ = OperationalPosition.objects.update_or_create(
        layout_version=axis.layout_version,
        code=f'{axis.code}.{suffix}',
        defaults=dict(
            name=f'{axis.name} {suffix.lower()}',
            position_kind=OperationalPosition.VEHICLE_SLOT,
            asset=axis.asset,
            axis=axis,
            chainage_m=0.0,
            latitude=point[1],
            longitude=point[0],
            logical_address={
                'generated_by': 'gate_axis_endpoint',
                'facility_role': role,
                'axis': axis.code,
            },
            is_active=axis.is_enabled,
        ),
    )
    stale = generated.exclude(pk=position.pk)
    RouteNode.objects.filter(operational_position__in=stale).update(
        operational_position=None,
        node_kind=RouteNode.AXIS_POINT,
    )
    stale.delete()
    endpoint = nodes[0]
    endpoint.operational_position = position
    endpoint.node_kind = RouteNode.ENTRY_EXIT
    endpoint.save(update_fields=['operational_position', 'node_kind'])
    return position


@transaction.atomic
def sync_axis_graph(axis, previous_points=None):
    points = axis.points or []
    if len(points) < 2:
        raise ValidationError('An axis requires at least two points.')
    connected_axes = _propagate_moved_road_vertices(axis, previous_points)
    stale_nodes = RouteNode.objects.filter(
        layout_version=axis.layout_version,
        asset=axis.asset,
        is_generated=True,
        metadata__axis_id=axis.pk,
    )
    removed = stale_nodes.filter(metadata__axis_index__gt=len(points))
    if RouteEdge.objects.filter(Q(from_node__in=removed) | Q(to_node__in=removed), is_generated=False).exists():
        raise ValidationError('Cannot remove axis nodes used by manual routing connections.')
    nodes = []
    for index, point in enumerate(points, start=1):
        node, _ = RouteNode.objects.update_or_create(
            layout_version=axis.layout_version,
            code=f'{axis.code}.N{index:03d}',
            defaults=dict(
                asset=axis.asset,
                name=f'{axis.name} point {index}',
                node_kind=RouteNode.AXIS_POINT,
                point={'type': 'Point', 'coordinates': list(map(float, point[:2]))},
                is_generated=True,
                is_enabled=axis.is_enabled,
                metadata={'axis_id': axis.pk, 'axis_index': index},
            ),
        )
        nodes.append(node)

    _sync_gate_endpoint_position(axis, nodes)

    movement_mode = _axis_movement_mode(axis.axis_kind)
    edge_ids = []
    for index, (first, second) in enumerate(zip(nodes, nodes[1:]), start=1):
        distance = geographic_distance_m(points[index - 1], points[index])
        directions = []
        if axis.direction in [LinearAxis.FORWARD, LinearAxis.BOTH]:
            directions.append(('F', first, second))
        if axis.direction in [LinearAxis.REVERSE, LinearAxis.BOTH]:
            directions.append(('R', second, first))
        for suffix, from_node, to_node in directions:
            edge, _ = RouteEdge.objects.update_or_create(
                layout_version=axis.layout_version,
                code=f'{axis.code}.E{index:03d}.{suffix}',
                defaults=dict(
                    name=f'{axis.name} segment {index}',
                    from_node=from_node,
                    to_node=to_node,
                    edge_kind=RouteEdge.TRAVEL,
                    movement_mode=movement_mode,
                    source_axis=axis,
                    access_policy=axis.access_policy,
                    distance_m=distance,
                    capacity=axis.capacity,
                    base_cost=max(distance, 0.01),
                    is_generated=True,
                    is_enabled=axis.is_enabled,
                ),
            )
            edge_ids.append(edge.pk)
    axis.route_edges.filter(is_generated=True).exclude(pk__in=edge_ids).delete()
    stale_nodes.exclude(pk__in=[node.pk for node in nodes]).delete()
    sync_road_junctions(axis.layout_version)
    for transfer_interface in axis.road_interfaces.all():
        sync_interface_graph(transfer_interface)
    for connected_axis in connected_axes:
        sync_axis_graph(connected_axis)
    return axis


@transaction.atomic
def sync_road_junctions(layout, tolerance_m=0.75):
    """Join snapped road vertices and geometric centerline intersections."""
    roads = list(LinearAxis.objects.filter(
        asset__layout_version=layout,
        axis_kind=LinearAxis.ROAD_CENTERLINE,
        is_enabled=True,
    ).select_related('asset', 'access_policy').order_by('pk'))
    road_axis_ids = [axis.pk for axis in roads]
    nodes = list(RouteNode.objects.filter(
        layout_version=layout,
        is_generated=True,
        is_enabled=True,
        metadata__axis_id__in=road_axis_ids,
    ).order_by('pk'))
    created = []
    edge_ids = []
    intersection_node_ids = []
    axis_nodes = {
        (node.metadata.get('axis_id'), node.metadata.get('axis_index')): node
        for node in nodes
    }

    def save_edge(code, from_node, to_node, capacity, policy, metadata):
        distance = geographic_distance_m(
            from_node.point['coordinates'], to_node.point['coordinates'],
        )
        edge, _ = RouteEdge.objects.update_or_create(
            layout_version=layout,
            code=code,
            defaults=dict(
                name='Road intersection',
                from_node=from_node,
                to_node=to_node,
                edge_kind=RouteEdge.TRAVEL,
                movement_mode=RouteEdge.ROAD,
                access_policy=policy,
                distance_m=distance,
                capacity=max(1, capacity),
                base_cost=max(distance, 0.01),
                is_generated=True,
                is_enabled=True,
                metadata=metadata,
            ),
        )
        created.append(edge)
        edge_ids.append(edge.pk)
        return edge

    for index, first in enumerate(nodes):
        first_axis_id = first.metadata.get('axis_id')
        first_point = first.point.get('coordinates', [])
        if len(first_point) < 2:
            continue
        for second in nodes[index + 1:]:
            if first_axis_id == second.metadata.get('axis_id'):
                continue
            second_point = second.point.get('coordinates', [])
            if len(second_point) < 2:
                continue
            distance = geographic_distance_m(first_point, second_point)
            if distance > tolerance_m:
                continue
            for suffix, from_node, to_node in [('F', first, second), ('R', second, first)]:
                save_edge(
                    f'AUTO.RJ.{first.pk}.{second.pk}.{suffix}', from_node, to_node,
                    1, None,
                    {
                        'connector_kind': 'road_junction',
                        'axis_ids': [first_axis_id, second.metadata.get('axis_id')],
                    },
                )

    def intersection_node(axis, segment_index, point, other_axis, other_segment_index):
        start = axis_nodes.get((axis.pk, segment_index + 1))
        end = axis_nodes.get((axis.pk, segment_index + 2))
        for candidate in [start, end]:
            if candidate and geographic_distance_m(candidate.point['coordinates'], point) <= tolerance_m:
                return candidate, False
        code = (
            f'AUTO.RI.A{axis.pk}.S{segment_index + 1}.'
            f'A{other_axis.pk}.S{other_segment_index + 1}'
        )
        node, _ = RouteNode.objects.update_or_create(
            layout_version=layout,
            code=code,
            defaults=dict(
                name='Road intersection',
                node_kind=RouteNode.JUNCTION,
                point={'type': 'Point', 'coordinates': point},
                asset=axis.asset,
                is_generated=True,
                is_enabled=True,
                metadata={
                    'connector_kind': 'road_intersection_node',
                    'intersection_axis_id': axis.pk,
                    'segment_index': segment_index + 1,
                    'other_axis_id': other_axis.pk,
                },
            ),
        )
        intersection_node_ids.append(node.pk)
        return node, True

    def connect_segment(axis, segment_index, node):
        start = axis_nodes.get((axis.pk, segment_index + 1))
        end = axis_nodes.get((axis.pk, segment_index + 2))
        if not start or not end:
            return
        metadata = {
            'connector_kind': 'road_intersection_access',
            'axis_id': axis.pk,
            'segment_index': segment_index + 1,
        }
        if axis.direction in [LinearAxis.FORWARD, LinearAxis.BOTH]:
            save_edge(
                f'AUTO.RI.N{node.pk}.A{axis.pk}.S{segment_index + 1}.FI',
                start, node, axis.capacity, axis.access_policy, metadata,
            )
            save_edge(
                f'AUTO.RI.N{node.pk}.A{axis.pk}.S{segment_index + 1}.FO',
                node, end, axis.capacity, axis.access_policy, metadata,
            )
        if axis.direction in [LinearAxis.REVERSE, LinearAxis.BOTH]:
            save_edge(
                f'AUTO.RI.N{node.pk}.A{axis.pk}.S{segment_index + 1}.RI',
                end, node, axis.capacity, axis.access_policy, metadata,
            )
            save_edge(
                f'AUTO.RI.N{node.pk}.A{axis.pk}.S{segment_index + 1}.RO',
                node, start, axis.capacity, axis.access_policy, metadata,
            )

    processed_intersections = set()
    for first_index, first_axis in enumerate(roads):
        for second_axis in roads[first_index + 1:]:
            for first_segment, first_points in enumerate(zip(first_axis.points, first_axis.points[1:])):
                for second_segment, second_points in enumerate(zip(second_axis.points, second_axis.points[1:])):
                    point = _segment_intersection(*first_points, *second_points)
                    if point is None:
                        continue
                    signature = (first_axis.pk, second_axis.pk, round(point[0], 10), round(point[1], 10))
                    if signature in processed_intersections:
                        continue
                    processed_intersections.add(signature)
                    first_node, first_virtual = intersection_node(
                        first_axis, first_segment, point, second_axis, second_segment,
                    )
                    second_node, second_virtual = intersection_node(
                        second_axis, second_segment, point, first_axis, first_segment,
                    )
                    if first_virtual:
                        connect_segment(first_axis, first_segment, first_node)
                    if second_virtual:
                        connect_segment(second_axis, second_segment, second_node)
                    if not first_virtual and not second_virtual:
                        continue
                    pair = sorted([first_node.pk, second_node.pk])
                    metadata = {
                        'connector_kind': 'road_intersection',
                        'axis_ids': [first_axis.pk, second_axis.pk],
                    }
                    save_edge(
                        f'AUTO.RI.J{pair[0]}.{pair[1]}.F', first_node, second_node,
                        min(first_axis.capacity, second_axis.capacity),
                        first_axis.access_policy or second_axis.access_policy, metadata,
                    )
                    save_edge(
                        f'AUTO.RI.J{pair[0]}.{pair[1]}.R', second_node, first_node,
                        min(first_axis.capacity, second_axis.capacity),
                        first_axis.access_policy or second_axis.access_policy, metadata,
                    )

    RouteEdge.objects.filter(
        layout_version=layout,
        is_generated=True,
        metadata__connector_kind__in=[
            'road_junction', 'road_intersection', 'road_intersection_access',
        ],
    ).exclude(pk__in=edge_ids).delete()
    RouteNode.objects.filter(
        layout_version=layout,
        is_generated=True,
        metadata__connector_kind='road_intersection_node',
    ).exclude(pk__in=intersection_node_ids).delete()
    return created


def _segment_intersection(first_start, first_end, second_start, second_end):
    """Return one WGS-84 crossing point for two non-collinear segments."""
    mean_latitude = math.radians(sum([
        float(first_start[1]), float(first_end[1]),
        float(second_start[1]), float(second_end[1]),
    ]) / 4)
    cosine = max(abs(math.cos(mean_latitude)), 1e-9)

    def planar(point):
        return float(point[0]) * cosine, float(point[1])

    p, p2 = planar(first_start), planar(first_end)
    q, q2 = planar(second_start), planar(second_end)
    r = (p2[0] - p[0], p2[1] - p[1])
    s = (q2[0] - q[0], q2[1] - q[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) < 1e-15:
        return None
    qp = (q[0] - p[0], q[1] - p[1])
    first_ratio = (qp[0] * s[1] - qp[1] * s[0]) / denominator
    second_ratio = (qp[0] * r[1] - qp[1] * r[0]) / denominator
    epsilon = 1e-9
    if not -epsilon <= first_ratio <= 1 + epsilon or not -epsilon <= second_ratio <= 1 + epsilon:
        return None
    return [
        float(first_start[0]) + (float(first_end[0]) - float(first_start[0])) * first_ratio,
        float(first_start[1]) + (float(first_end[1]) - float(first_start[1])) * first_ratio,
    ]


def _interface_hub_point(transfer_interface):
    points = []
    for position in transfer_interface.positions.select_related(
        'transfer_interface__reference_che', 'transfer_interface__axis',
    ):
        point = resolved_interface_point(position)
        if point:
            points.append(point)
    if points:
        return [
            sum(float(point[0]) for point in points) / len(points),
            sum(float(point[1]) for point in points) / len(points),
        ]
    return _geometry_center(transfer_interface.geometry or transfer_interface.asset.geometry)


@transaction.atomic
def sync_interface_graph(transfer_interface):
    """Compile aggregate asset/interface hubs and one optional road attachment."""
    layout = transfer_interface.layout_version
    asset = transfer_interface.asset
    asset_point = _geometry_center(asset.geometry)
    interface_point = _interface_hub_point(transfer_interface) or asset_point
    if not asset_point or not interface_point:
        raise ValidationError('Asset and transfer interface geometry require valid coordinates.')

    asset_node, _ = RouteNode.objects.update_or_create(
        layout_version=layout,
        code=f'A{asset.pk}.NODE',
        defaults=dict(
            asset=asset,
            name=f'{asset.code} area',
            node_kind=RouteNode.OPERATIONAL,
            point={'type': 'Point', 'coordinates': asset_point},
            is_generated=True,
            is_enabled=asset.is_active,
            metadata={'aggregate_kind': 'asset', 'asset_id': asset.pk},
        ),
    )
    interface_node, _ = RouteNode.objects.update_or_create(
        transfer_interface=transfer_interface,
        defaults=dict(
            layout_version=layout,
            code=f'A{asset.pk}.IF{transfer_interface.pk}.NODE',
            asset=asset,
            name=f'{transfer_interface.code} interface',
            node_kind=RouteNode.INTERFACE,
            point={'type': 'Point', 'coordinates': interface_point},
            is_generated=True,
            is_enabled=transfer_interface.is_enabled,
            metadata={
                'aggregate_kind': 'interface',
                'asset_id': asset.pk,
                'interface_id': transfer_interface.pk,
            },
        ),
    )

    edge_ids = []
    mode = _asset_interface_mode(asset)
    distance = geographic_distance_m(asset_point, interface_point)
    for suffix, from_node, to_node in [('OUT', asset_node, interface_node), ('IN', interface_node, asset_node)]:
        edge, _ = RouteEdge.objects.update_or_create(
            layout_version=layout,
            code=f'AUTO.A{asset.pk}.IF{transfer_interface.pk}.{suffix}',
            defaults=dict(
                name=f'{asset.code} to {transfer_interface.code}',
                from_node=from_node,
                to_node=to_node,
                edge_kind=RouteEdge.HANDLE,
                movement_mode=mode,
                source_axis=None,
                access_policy=transfer_interface.access_policy or asset.access_policy,
                distance_m=distance,
                capacity=transfer_interface.capacity,
                base_cost=max(distance, 0.01),
                is_generated=True,
                is_enabled=transfer_interface.is_enabled and asset.is_active,
                metadata={
                    'connector_kind': 'asset_interface',
                    'interface_id': transfer_interface.pk,
                    'operational_axis_id': transfer_interface.axis_id,
                },
            ),
        )
        edge_ids.append(edge.pk)

    position_nodes = RouteNode.objects.filter(
        operational_position__interface_position__transfer_interface=transfer_interface,
    ).select_related('operational_position__interface_position')
    for position_node in position_nodes:
        position_node.node_kind = RouteNode.OPERATIONAL
        position_node.metadata = {
            **(position_node.metadata or {}),
            'aggregate_kind': 'interface_position',
            'interface_id': transfer_interface.pk,
        }
        position_node.save(update_fields=['node_kind', 'metadata'])
        position_point = resolved_route_node_point(position_node)
        position_distance = geographic_distance_m(
            position_point['coordinates'], interface_point,
        ) if position_point else 0.0
        position_id = position_node.operational_position.interface_position_id
        for suffix, from_node, to_node in [('OUT', interface_node, position_node), ('IN', position_node, interface_node)]:
            edge, _ = RouteEdge.objects.update_or_create(
                layout_version=layout,
                code=f'AUTO.IF{transfer_interface.pk}.P{position_id}.{suffix}',
                defaults=dict(
                    name=f'{transfer_interface.code} position access',
                    from_node=from_node,
                    to_node=to_node,
                    edge_kind=RouteEdge.HANDOFF,
                    movement_mode=RouteEdge.STATIC,
                    access_policy=transfer_interface.access_policy,
                    distance_m=position_distance,
                    capacity=position_node.operational_position.interface_position.capacity,
                    base_cost=max(position_distance, 0.01),
                    is_generated=True,
                    is_enabled=transfer_interface.is_enabled,
                    metadata={
                        'connector_kind': 'interface_position',
                        'interface_id': transfer_interface.pk,
                        'interface_position_id': position_id,
                    },
                ),
            )
            edge_ids.append(edge.pk)

    road = transfer_interface.road_axis
    if road and road.axis_kind == LinearAxis.ROAD_CENTERLINE and road.is_enabled:
        road_nodes = list(RouteNode.objects.filter(
            layout_version=layout,
            is_generated=True,
            metadata__axis_id=road.pk,
        ))
        if road_nodes:
            road_node = min(
                road_nodes,
                key=lambda node: geographic_distance_m(interface_point, node.point['coordinates']),
            )
            road_distance = geographic_distance_m(interface_point, road_node.point['coordinates'])
            for suffix, from_node, to_node in [('OUT', interface_node, road_node), ('IN', road_node, interface_node)]:
                edge, _ = RouteEdge.objects.update_or_create(
                    layout_version=layout,
                    code=f'AUTO.IF{transfer_interface.pk}.ROAD{road.pk}.{suffix}',
                    defaults=dict(
                        name=f'{transfer_interface.code} road access',
                        from_node=from_node,
                        to_node=to_node,
                        edge_kind=RouteEdge.HANDOFF,
                        movement_mode=RouteEdge.ROAD,
                        access_policy=transfer_interface.access_policy or road.access_policy,
                        distance_m=road_distance,
                        capacity=min(transfer_interface.capacity, road.capacity),
                        base_cost=max(road_distance, 0.01),
                        is_generated=True,
                        is_enabled=transfer_interface.is_enabled,
                        metadata={
                            'connector_kind': 'interface_road',
                            'interface_id': transfer_interface.pk,
                            'road_axis_id': road.pk,
                        },
                    ),
                )
                edge_ids.append(edge.pk)

    RouteEdge.objects.filter(
        layout_version=layout,
        is_generated=True,
        metadata__interface_id=transfer_interface.pk,
    ).exclude(pk__in=edge_ids).delete()
    return interface_node


@transaction.atomic
def sync_axis_markers(
    axis,
    marker_kind,
    count,
    default_spacing_m,
    start_margin_m=0.0,
    end_margin_m=0.0,
    spacings=None,
    reverse=False,
    lateral_offset_m=0.0,
):
    count = int(count)
    if count < 1:
        raise ValidationError('At least one marker is required.')
    length = polyline_length_m(axis.points)
    gaps = count - 1
    values = [float(default_spacing_m)] * gaps if spacings is None else [float(value) for value in spacings]
    if len(values) != gaps:
        raise ValidationError(f'Provide exactly {gaps} marker spacing values.')
    if any(value <= 0 for value in values):
        raise ValidationError('Marker spacings must be greater than zero.')
    if float(start_margin_m) + sum(values) + float(end_margin_m) > length + 0.05:
        raise ValidationError('Marker spacings and margins exceed the axis length.')

    axis.markers.filter(marker_kind=marker_kind).delete()
    chainage = length - float(end_margin_m) if reverse else float(start_margin_m)
    markers = []
    for sequence in range(1, count + 1):
        spacing = None if sequence == 1 else values[sequence - 2]
        if spacing is not None:
            chainage += -spacing if reverse else spacing
        point = point_along_axis(axis.points, chainage, lateral_offset_m)
        markers.append(LinearMarker.objects.create(
            axis=axis,
            marker_kind=marker_kind,
            sequence=sequence,
            code=f'{axis.code}.M{sequence:03d}',
            name=str(sequence),
            chainage_m=chainage,
            lateral_offset_m=lateral_offset_m,
            longitude=point[0],
            latitude=point[1],
            metadata={'spacing_from_previous_m': spacing},
        ))
    return markers


@transaction.atomic
def sync_interface_positions(transfer_interface, count=None):
    if hasattr(transfer_interface, '_resolved_position_count'):
        del transfer_interface._resolved_position_count
    previous_count = transfer_interface.positions.aggregate(value=Max('position_index'))['value']
    count = int(count or previous_count or transfer_interface.capacity or 1)
    transfer_interface.full_clean()
    stale = transfer_interface.positions.filter(Q(lane_index__gt=transfer_interface.lane_count) | Q(position_index__gt=count))
    operational_ids = OperationalPosition.objects.filter(interface_position__in=stale).values_list('pk', flat=True)
    from minitos_core.models import OrderLeg
    if OrderLeg.objects.filter(Q(start_position_id__in=operational_ids) | Q(end_position_id__in=operational_ids)).exists() or RouteEdge.objects.filter(
        Q(from_node__operational_position_id__in=operational_ids) | Q(to_node__operational_position_id__in=operational_ids), is_generated=False,
    ).exists():
        raise ValidationError('Cannot remove transfer positions referenced by orders or routing connections.')
    geometry = transfer_interface.geometry or transfer_interface.asset.geometry
    geometry_type = geometry.get('type') if isinstance(geometry, dict) else None
    coordinates = geometry.get('coordinates', []) if isinstance(geometry, dict) else []
    if geometry_type == 'Point':
        points = [coordinates for _ in range(count)]
    elif geometry_type == 'LineString' and len(coordinates) >= 2:
        length = polyline_length_m(coordinates)
        points = [point_along_axis(coordinates, length * ((index + 0.5) / count)) for index in range(count)]
    elif geometry_type == 'Polygon' and coordinates and coordinates[0]:
        ring = coordinates[0]
        longitude = sum(float(point[0]) for point in ring) / len(ring)
        latitude = sum(float(point[1]) for point in ring) / len(ring)
        points = [[longitude, latitude] for _ in range(count)]
    else:
        raise ValidationError('Transfer interface geometry must be a Point, LineString, or Polygon.')

    created = []
    # Move sequences out of the way while re-numbering a resized lane matrix.
    maximum = transfer_interface.positions.aggregate(value=Max('sequence'))['value'] or 0
    transfer_interface.positions.update(sequence=F('sequence') + maximum + count * transfer_interface.lane_count + 1)
    sign = 1 if transfer_interface.side in ('left', 'end', 'internal') else -1
    for lane in range(1, transfer_interface.lane_count + 1):
        for index, point in enumerate(points, start=1):
            lateral = sign * (lane - 1) * transfer_interface.lane_spacing_m
            if geometry_type == 'LineString':
                point = point_along_axis(coordinates, polyline_length_m(coordinates) * ((index - 0.5) / count), lateral)
            elif lateral:
                angle = math.radians(transfer_interface.asset.heading_deg)
                lat, lon = offset_geographic(point[1], point[0], -math.cos(angle) * lateral, math.sin(angle) * lateral)
                point = [lon, lat]
            code = f'{transfer_interface.code}.{index:02d}' if lane == 1 else f'{transfer_interface.code}.L{lane}.{index:02d}'
            interface_position, _ = InterfacePosition.objects.update_or_create(
                transfer_interface=transfer_interface, lane_index=lane, position_index=index,
                defaults=dict(sequence=(lane - 1) * count + index, code=code,
                              name=f'{transfer_interface.name} L{lane} / P{index}',
                              longitude=float(point[0]), latitude=float(point[1]),
                              heading_deg=transfer_interface.asset.heading_deg),
            )
            operational, _ = OperationalPosition.objects.update_or_create(
                interface_position=interface_position,
                defaults=dict(
                    layout_version=transfer_interface.layout_version,
                    code=code,
                    name=interface_position.name,
                    position_kind=OperationalPosition.TRANSFER_POSITION,
                    asset=transfer_interface.asset,
                    axis=transfer_interface.axis,
                    longitude=interface_position.longitude,
                    latitude=interface_position.latitude,
                    logical_address={'interface': transfer_interface.code, 'lane': lane, 'position': index},
                ),
            )
            RouteNode.objects.update_or_create(
                operational_position=operational,
                defaults=dict(
                    layout_version=transfer_interface.layout_version,
                    code=f'{code}.NODE',
                    name=interface_position.name,
                    node_kind=RouteNode.OPERATIONAL,
                    point={'type': 'Point', 'coordinates': [interface_position.longitude, interface_position.latitude]},
                    asset=transfer_interface.asset,
                    is_generated=True,
                    is_enabled=transfer_interface.is_enabled,
                    metadata={
                        'aggregate_kind': 'interface_position',
                        'interface_id': transfer_interface.pk,
                    },
                ),
            )
            created.append(interface_position)
    transfer_interface.positions.exclude(pk__in=[position.pk for position in created]).delete()
    sync_interface_graph(transfer_interface)
    return created


def nearest_axis_chainage(points, point):
    """Project a GPS point onto a logical axis, returning clamped meter chainage."""
    total, best = 0.0, (float('inf'), 0.0)
    latitude = math.radians(float(point[1]))
    def meters(value):
        return (math.radians(value[0] - point[0]) * EARTH_RADIUS_M * math.cos(latitude),
                math.radians(value[1] - point[1]) * EARTH_RADIUS_M)
    for start, end in zip(points, points[1:]):
        x, y = meters(start)
        ex, ey = meters(end)
        dx, dy = ex - x, ey - y
        ratio = max(0.0, min(1.0, -(x * dx + y * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
        length = geographic_distance_m(start, end)
        candidate = (math.hypot(x + ratio * dx, y + ratio * dy), total + ratio * length)
        best = min(best, candidate)
        total += length
    return best[1]


def resolved_interface_point(position):
    interface = position.transfer_interface
    if interface.position_reference != 'che':
        return [position.longitude, position.latitude]
    che = interface.reference_che
    if che.latitude is None or che.longitude is None:
        return None
    count = getattr(interface, '_resolved_position_count', None)
    if count is None:
        count = max((p.position_index for p in interface.positions.all()), default=1)
        interface._resolved_position_count = count
    forward = interface.longitudinal_offset_m + (position.position_index - (count + 1) / 2) * interface.position_spacing_m
    sign = 1 if interface.side in ('left', 'end', 'internal') else -1
    lateral = interface.lateral_offset_m + sign * (position.lane_index - 1) * interface.lane_spacing_m
    if interface.axis_id:
        points = interface.axis.points
        chainage = nearest_axis_chainage(points, [che.longitude, che.latitude]) + forward
        return point_along_axis(points, chainage, lateral)
    angle = math.radians(che.rotation)
    lat, lon = offset_geographic(che.latitude, che.longitude,
                                math.sin(angle) * forward - math.cos(angle) * lateral,
                                math.cos(angle) * forward + math.sin(angle) * lateral)
    return [lon, lat]


def resolved_route_node_point(node):
    if node.transfer_interface_id:
        point = _interface_hub_point(node.transfer_interface)
        return {'type': 'Point', 'coordinates': point} if point else None
    if node.operational_position_id and node.operational_position.interface_position_id:
        point = resolved_interface_point(node.operational_position.interface_position)
        return {'type': 'Point', 'coordinates': point} if point else None
    return node.point


def sync_block_crane_rail(asset):
    if asset.asset_kind != LayoutAsset.YARD_BLOCK:
        return
    grid = asset.storage_grid
    rail = asset.linear_axes.filter(axis_kind=LinearAxis.CRANE_RAIL, code=f'{asset.code}.RAIL').first()
    if not rail:
        return
    bay_length = grid.max_bays * grid.pile_length_m + (grid.max_bays - 1) * grid.bay_spacing_m
    row_width = grid.max_rows * grid.pile_width_m + (grid.max_rows - 1) * grid.row_spacing_m
    forward = (bay_length if grid.bay_use_heading_axis else row_width) + (grid.padding_top_m or 0) + (grid.padding_bottom_m or 0)
    right = (row_width if grid.bay_use_heading_axis else bay_length) + (grid.padding_left_m or 0) + (grid.padding_right_m or 0)
    sides = asset.metadata.get('editor_transfers', {}).get('sides', {})
    def extension(side):
        definition = sides.get(side, {})
        return float(definition.get('offset_m', 0)) + (int(definition.get('lane_count', 1)) - 1) * float(definition.get('lane_spacing_m', 3.5)) if definition.get('enabled') else 0
    angle = math.radians(grid.heading_deg or 0)
    def point(distance):
        lat, lon = offset_geographic(grid.latitude, grid.longitude,
                                    math.sin(angle) * distance + math.cos(angle) * right / 2,
                                    math.cos(angle) * distance - math.sin(angle) * right / 2)
        return [lon, lat]
    rail.points = [point(-extension('bottom')), point(forward + extension('top'))]
    rail.save(update_fields=['points'])
    sync_axis_graph(rail)


@transaction.atomic
def move_asset_dependents(asset, old_geometry, old_heading, skip_root_road_axes=False):
    """Apply a rigid WGS84 transform to attached layout records without changing IDs."""
    def anchor(geometry):
        coordinates = geometry.get('coordinates', [])
        return coordinates[0][0] if geometry.get('type') == 'Polygon' else coordinates[0] if geometry.get('type') == 'LineString' else coordinates
    old, new = anchor(old_geometry or {}), anchor(asset.geometry or {})
    if not old or not new or (old == new and old_heading == asset.heading_deg):
        return
    angle = math.radians(asset.heading_deg - old_heading)
    def point(value):
        east = math.radians(value[0] - old[0]) * EARTH_RADIUS_M * math.cos(math.radians(old[1]))
        north = math.radians(value[1] - old[1]) * EARTH_RADIUS_M
        lat, lon = offset_geographic(new[1], new[0], east * math.cos(angle) + north * math.sin(angle),
                                    north * math.cos(angle) - east * math.sin(angle))
        return [lon, lat]
    def geometry(value):
        if not value:
            return value
        coords = value['coordinates']
        transformed = point(coords) if value['type'] == 'Point' else [point(p) for p in coords] if value['type'] == 'LineString' else [[point(p) for p in ring] for ring in coords]
        return {**value, 'coordinates': transformed}
    pending, seen = [asset], set()
    from .yard_grid import sync_yard_grid
    while pending:
        current = pending.pop()
        if current.pk in seen:
            raise ValidationError('Asset hierarchy cannot contain cycles.')
        seen.add(current.pk)
        pending.extend(current.children.all())
        if current.pk != asset.pk:
            current.geometry = geometry(current.geometry)
            current.heading_deg = (current.heading_deg + asset.heading_deg - old_heading) % 360
            current.save(update_fields=['geometry', 'heading_deg'])
        for axis in current.linear_axes.all():
            if skip_root_road_axes and current.pk == asset.pk and axis.axis_kind == LinearAxis.ROAD_CENTERLINE:
                continue
            axis.points = [point(p) for p in axis.points]
            axis.save(update_fields=['points'])
            sync_axis_graph(axis)
            for marker in axis.markers.all():
                marker.longitude, marker.latitude = point_along_axis(axis.points, marker.chainage_m, marker.lateral_offset_m)
                marker.save(update_fields=['longitude', 'latitude'])
        try:
            grid = current.storage_grid
        except StorageGrid.DoesNotExist:
            pass
        else:
            grid.longitude, grid.latitude = point([grid.longitude, grid.latitude])
            grid.heading_deg = ((grid.heading_deg or 0) + asset.heading_deg - old_heading) % 360
            grid.save(update_fields=['longitude', 'latitude', 'heading_deg'])
            sync_yard_grid(grid)
        for interface in current.transfer_interfaces.all():
            interface.geometry = geometry(interface.geometry)
            interface.save(update_fields=['geometry'])
            sync_interface_positions(interface)
        for position in current.operational_positions.filter(stack_position__isnull=True, interface_position__isnull=True, latitude__isnull=False, longitude__isnull=False):
            position.longitude, position.latitude = point([position.longitude, position.latitude])
            position.save(update_fields=['longitude', 'latitude'])
            RouteNode.objects.filter(operational_position=position).update(point={'type': 'Point', 'coordinates': [position.longitude, position.latitude]})
        for node in current.route_nodes.filter(is_generated=False, operational_position__isnull=True):
            node.point = geometry(node.point)
            node.save(update_fields=['point'])
        for che in current.current_ches.filter(latitude__isnull=False, longitude__isnull=False):
            che.longitude, che.latitude = point([che.longitude, che.latitude])
            che.rotation = (che.rotation + asset.heading_deg - old_heading) % 360
            che.save(update_fields=['longitude', 'latitude', 'rotation'])


def validate_layout(layout):
    errors = []
    warnings = []
    for asset in layout.assets.all():
        geometry = asset.geometry or {}
        if geometry.get('type') not in {'Point', 'LineString', 'Polygon'}:
            errors.append({'code': 'asset_geometry', 'object': asset.code, 'message': 'Asset geometry is missing or invalid.'})
    for axis in LinearAxis.objects.filter(asset__layout_version=layout):
        if len(axis.points or []) < 2:
            errors.append({'code': 'axis_points', 'object': axis.code, 'message': 'Axis requires at least two points.'})
        if not axis.route_edges.filter(is_generated=True).exists():
            warnings.append({'code': 'axis_not_compiled', 'object': axis.code, 'message': 'Axis has no generated route edges.'})
    for interface in TransferInterface.objects.filter(asset__layout_version=layout).select_related('road_axis'):
        if not interface.positions.exists():
            warnings.append({'code': 'interface_positions', 'object': interface.code, 'message': 'Transfer interface has no positions.'})
        if not RouteNode.objects.filter(transfer_interface=interface).exists():
            warnings.append({'code': 'interface_not_compiled', 'object': interface.code, 'message': 'Transfer interface has no aggregate route node.'})
        if interface.requires_vehicle_present and not interface.road_axis_id:
            warnings.append({'code': 'interface_without_road', 'object': interface.code, 'message': 'Vehicle interface is not attached to an adjacent road.'})

    nodes = list(layout.route_nodes.filter(is_enabled=True).select_related('asset').order_by('code'))
    if len(nodes) > 1:
        node_ids = {node.pk for node in nodes}
        adjacency = {node_id: set() for node_id in node_ids}
        for first, second in layout.route_edges.filter(is_enabled=True).values_list('from_node_id', 'to_node_id'):
            if first in node_ids and second in node_ids:
                adjacency[first].add(second)
                adjacency[second].add(first)
        components = []
        unseen = set(node_ids)
        while unseen:
            component = set()
            stack = [min(unseen)]
            while stack:
                node_id = stack.pop()
                if node_id in component:
                    continue
                component.add(node_id)
                stack.extend(adjacency[node_id] - component)
            unseen -= component
            components.append(component)
        components.sort(key=lambda item: (-len(item), min(item)))
        if len(components) > 1:
            node_by_id = {node.pk: node for node in nodes}
            def component_summary(component):
                members = [node_by_id[node_id] for node_id in sorted(component)]
                assets = sorted({node.asset.code for node in members if node.asset_id})
                return {
                    'node_count': len(members),
                    'asset_codes': assets,
                    'nodes': [
                        {
                            'id': node.pk,
                            'code': node.code,
                            'name': node.name,
                            'kind': node.node_kind,
                            'asset_code': node.asset.code if node.asset_id else None,
                        }
                        for node in members[:20]
                    ],
                    'omitted_node_count': max(0, len(members) - 20),
                }
            main_component = component_summary(components[0])
            disconnected = [component_summary(component) for component in components[1:]]
            outside_count = sum(item['node_count'] for item in disconnected)
            warnings.append({
                'code': 'graph_components',
                'object': 'Routing network',
                'message': (
                    f'Routing network is split into {len(components)} disconnected groups. '
                    f'{outside_count} node(s) cannot reach the largest group of '
                    f'{main_component["node_count"]} node(s).'
                ),
                'hint': (
                    'Connect one road endpoint or transfer interface from each listed group '
                    'to the main road network, or remove unused infrastructure.'
                ),
                'main_component': main_component,
                'disconnected_components': disconnected,
            })
    return {'is_valid': not errors, 'errors': errors, 'warnings': warnings}


@transaction.atomic
def publish_layout(layout):
    result = validate_layout(layout)
    if not result['is_valid']:
        raise ValidationError('Resolve layout validation errors before publishing.')
    LayoutVersion.objects.filter(location=layout.location, is_current=True).exclude(pk=layout.pk).update(
        is_current=False,
        status=LayoutVersion.ARCHIVED,
    )
    layout.status = LayoutVersion.PUBLISHED
    layout.is_current = True
    layout.published_at = timezone.now()
    layout.save(update_fields=['status', 'is_current', 'published_at', 'updated_at'])
    return layout
