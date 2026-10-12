import heapq
from collections import defaultdict
from itertools import count

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from minitos_core.models import (
    OperationalPosition,
    OrderLeg,
    OrderLegRouteEdge,
    Order,
    OrderRoutePlan,
    RouteEdge,
    RouteNode,
)


def _endpoint_node(position):
    node = RouteNode.objects.filter(
        operational_position=position,
        is_enabled=True,
    ).first()
    if node:
        return node
    if position.asset_id:
        node = RouteNode.objects.filter(
            layout_version=position.layout_version,
            asset_id=position.asset_id,
            metadata__aggregate_kind='asset',
            is_enabled=True,
        ).first()
        if node:
            return node
    raise ValidationError(
        f'Position {position.code} is not connected to the routing graph.'
    )


def _shortest_path(layout, start_node, end_node):
    if start_node.pk == end_node.pk:
        return []
    adjacency = defaultdict(list)
    for edge in RouteEdge.objects.filter(
        layout_version=layout,
        is_enabled=True,
        from_node__is_enabled=True,
        to_node__is_enabled=True,
    ).select_related('from_node', 'to_node').order_by('pk'):
        adjacency[edge.from_node_id].append(edge)

    serial = count()
    pending = [(0.0, next(serial), start_node.pk)]
    best = {start_node.pk: 0.0}
    previous = {}
    while pending:
        cost, _, node_id = heapq.heappop(pending)
        if cost > best.get(node_id, float('inf')):
            continue
        if node_id == end_node.pk:
            break
        for edge in adjacency.get(node_id, []):
            candidate = cost + max(0.0, float(edge.base_cost or 0.0))
            if candidate >= best.get(edge.to_node_id, float('inf')):
                continue
            best[edge.to_node_id] = candidate
            previous[edge.to_node_id] = edge
            heapq.heappush(pending, (candidate, next(serial), edge.to_node_id))

    if end_node.pk not in previous:
        raise ValidationError(
            f'No enabled route connects {start_node.code} to {end_node.code}.'
        )
    path = []
    node_id = end_node.pk
    while node_id != start_node.pk:
        edge = previous[node_id]
        path.append(edge)
        node_id = edge.from_node_id
    return list(reversed(path))


def _path_groups(path):
    groups = []
    for edge in path:
        if not groups or groups[-1][0] != edge.movement_mode:
            groups.append((edge.movement_mode, [edge]))
        else:
            groups[-1][1].append(edge)
    return groups


def _transition_position(node):
    if node.operational_position_id:
        return node.operational_position
    if node.transfer_interface_id:
        return OperationalPosition.objects.filter(
            interface_position__transfer_interface_id=node.transfer_interface_id,
            interface_position__is_enabled=True,
            is_active=True,
        ).order_by(
            'interface_position__lane_index',
            'interface_position__position_index',
        ).first()
    return None


def _leg_kind(mode, edges):
    if any(edge.edge_kind == RouteEdge.WAIT for edge in edges):
        return OrderLeg.WAIT
    if mode == RouteEdge.CRANE:
        return OrderLeg.HANDLE
    if mode == RouteEdge.STATIC:
        return OrderLeg.HANDOFF
    return OrderLeg.MOVE


@transaction.atomic
def plan_order_route(order):
    if not order.work_queue_id or not order.work_queue.location_id:
        raise ValidationError('A routed order requires a terminal work queue.')
    if not order.from_position_id or not order.to_position_id:
        raise ValidationError('A routed order requires start and end positions.')
    start = order.from_position
    finish = order.to_position
    if start.layout_version_id != finish.layout_version_id:
        raise ValidationError('Order positions must belong to the same layout version.')
    layout = start.layout_version
    if layout.location_id != order.work_queue.location_id:
        raise ValidationError('Order positions must belong to the work queue terminal.')
    if not layout.is_current:
        raise ValidationError('Order routes must use the terminal current layout.')
    if order.container_number_id and order.container_number.location_id != layout.location_id:
        raise ValidationError('The container visit belongs to another terminal.')
    if start.pk == finish.pk:
        raise ValidationError('Order start and end positions must be different.')

    start_node = _endpoint_node(start)
    end_node = _endpoint_node(finish)
    path = _shortest_path(layout, start_node, end_node)
    groups = _path_groups(path)
    if not groups:
        groups = [(RouteEdge.CRANE, [])]

    version = (order.route_plans.aggregate(value=Max('version'))['value'] or 0) + 1
    order.route_plans.filter(status=OrderRoutePlan.ACTIVE).update(
        status=OrderRoutePlan.SUPERSEDED,
    )
    plan = OrderRoutePlan.objects.create(
        order=order,
        layout_version=layout,
        version=version,
        status=OrderRoutePlan.ACTIVE,
        cost=sum(float(edge.base_cost or 0.0) for edge in path),
        reason='Shortest enabled route by base cost.',
    )

    leg_start = start
    for index, (mode, edges) in enumerate(groups, start=1):
        final_group = index == len(groups)
        leg_end = finish if final_group else _transition_position(edges[-1].to_node)
        if not leg_end:
            raise ValidationError(
                f'Route transition at {edges[-1].to_node.code} has no operational position.'
            )
        leg = OrderLeg.objects.create(
            order=order,
            route_plan=plan,
            sequence=index,
            leg_kind=_leg_kind(mode, edges),
            start_position=leg_start,
            end_position=leg_end,
            distance=sum(float(edge.distance_m or 0.0) for edge in edges),
        )
        OrderLegRouteEdge.objects.bulk_create([
            OrderLegRouteEdge(order_leg=leg, route_edge=edge, sequence=edge_index)
            for edge_index, edge in enumerate(edges, start=1)
        ])
        leg_start = leg_end
    order.status = Order.PLANNED
    order.completed_at = None
    order.released_at = None
    order.save(update_fields=['status', 'completed_at', 'released_at'])
    return plan
