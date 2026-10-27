"""TCL-006 ``import_v1``: one vessel discharge into one yard block.

Seeds the smallest terminal the Sprint 1 demo needs (see the Sprint 1 note,
demo steps 1-9):

* Location "Test Terminal", Enterprise ``terminal-velocity``, Site ``test-terminal``;
* one quay (QUAY-01 / BERTH-01 with bollards), one yard block (BLOCK01, the
  no-show exception block), one road (ROAD-01) and two truck hand-off lanes
  (QC01 lane under the crane, BLOCK01 transfer lane);
* CHEs QC01 (STS), TT01, TT02 (TT) and RTG01 (RTG);
* vessel visit V001 with ``containers`` (default 1, max 5) pre-advised import
  containers, each with a PLANNED discharge order (QC -> TT -> RTG legs).

Everything is deterministic: auto-increment ids restart on reset, Enterprise
and Site ids are uuid5 under ``base.SCENARIO_NAMESPACE``, and the only
"random" choices (container serials, weights, yard slots) come from
``random.Random(seed)``.
"""

import random
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import (
    AccessPolicy, BerthSegment, CarrierVisit, Che, CheOperatingScope,
    ContainerOwner, ContainerUnit, ContainerVisit, Enterprise, EquipmentType,
    ISOType, LayoutAsset, LinearAxis, Location, OperationalPosition, Order,
    OrderLeg, OrderLegRouteEdge, OrderRoutePlan, Position, RouteEdge, RouteNode,
    Site, StorageGrid, TransferInterface, WorkQueue,
)
from ..services import (
    current_layout, geographic_distance_m, local_to_geographic, publish_layout,
    sync_axis_graph, sync_berth_bollards, sync_interface_positions, sync_yard_grid,
)
from ..services.order_execution import EXCEPTION_BLOCK_NAME, release_order
from ..services.routing import _endpoint_node, _shortest_path
from ..services.site_sync import (
    DEFAULT_ENTERPRISE_CODE, DEFAULT_ENTERPRISE_NAME, _emit_site_created,
)
from .base import container_number, scenario_uuid

NAME = 'import_v1'
DEFAULT_SEED = 20261026
MAX_CONTAINERS = 5

LOCATION_NAME = 'Test Terminal'
SITE_CODE = 'test-terminal'
VISIT_NAME = 'V001'
OWNER_CODE = 'TVLU'  # Fictional owner prefix; check digits are real ISO 6346.
WORK_QUEUE_NAME = 'QC01-WQ'
# Scenario clock: 2026-10-26 06:00 UTC (Sprint 1 start). Business times are
# constants so a rerun produces the same visit/order data.
SCENARIO_EPOCH = datetime(2026, 10, 26, 6, 0, tzinfo=dt_timezone.utc)
# Vessel stowage for container n: bay 034, row 06, tier 82 + 2n (ISO 9711).
VESSEL_BAY, VESSEL_ROW, VESSEL_FIRST_TIER = 34, 6, 82


def _coordinate(location, x_m, y_m):
    latitude, longitude = local_to_geographic(location, x_m, y_m)
    return [longitude, latitude]


def _rectangle(location, x_m, y_m, width_m, height_m):
    points = [
        _coordinate(location, x_m, y_m),
        _coordinate(location, x_m + width_m, y_m),
        _coordinate(location, x_m + width_m, y_m + height_m),
        _coordinate(location, x_m, y_m + height_m),
    ]
    return {'type': 'Polygon', 'coordinates': [[*points, points[0]]]}


def _line(location, *points):
    return {'type': 'LineString', 'coordinates': [_coordinate(location, *point) for point in points]}


def _seed_equipment_types():
    types = {}
    for code, name, flags, dimensions in [
        ('STS', 'Ship-to-Shore Crane', {'is_quay_equipment': True}, (30.0, 27.0, 70.0)),
        ('TT', 'Terminal Truck', {'is_horizontal_transport': True}, (7.0, 2.6, 3.2)),
        ('RTG', 'Rubber Tyred Gantry', {'is_yard_equipment': True}, (26.0, 11.0, 20.0)),
    ]:
        types[code] = EquipmentType.objects.create(
            name=name, equipment_type=code,
            length_m=dimensions[0], width_m=dimensions[1], height_m=dimensions[2],
            **flags,
        )
    return types


def _seed_site(location):
    enterprise = Enterprise.objects.create(
        id=scenario_uuid('enterprise', DEFAULT_ENTERPRISE_CODE),
        code=DEFAULT_ENTERPRISE_CODE,
        name=DEFAULT_ENTERPRISE_NAME,
    )
    # Same code ensure_site_for_location would derive from "Test Terminal",
    # but with a uuid5 id so the UNS identity is stable across resets.
    site = Site.objects.create(
        id=scenario_uuid('site', DEFAULT_ENTERPRISE_CODE, SITE_CODE),
        enterprise=enterprise,
        location=location,
        code=SITE_CODE,
        simulation_enabled=True,
    )
    _emit_site_created(site)
    return site


def _handoff_edges(layout, road_axis, interface, policy):
    """Connect each interface position to its nearest road node (both ways)."""
    road_nodes = list(RouteNode.objects.filter(metadata__axis_id=road_axis.pk).order_by('code'))
    for interface_node in RouteNode.objects.filter(
        operational_position__interface_position__transfer_interface=interface,
    ).order_by('code'):
        road_node = min(
            road_nodes,
            key=lambda node: (
                geographic_distance_m(node.point['coordinates'], interface_node.point['coordinates']),
                node.code,
            ),
        )
        position_code = interface_node.operational_position.code
        for suffix, start, end in [('IN', road_node, interface_node), ('OUT', interface_node, road_node)]:
            RouteEdge.objects.create(
                layout_version=layout,
                code=f'{position_code}.{suffix}',
                name=f'{interface.code} transfer {suffix.lower()}',
                from_node=start,
                to_node=end,
                edge_kind=RouteEdge.HANDOFF,
                movement_mode=RouteEdge.ROAD,
                access_policy=policy,
                distance_m=geographic_distance_m(start.point['coordinates'], end.point['coordinates']),
                capacity=1,
                base_cost=1,
            )


def _seed_layout(location, types):
    layout = current_layout(location)
    policy = AccessPolicy.objects.create(
        layout_version=layout, code='TT-ROAD', name='Terminal trucks on roads',
        allow_loaded=True, allow_empty=True,
    )
    policy.allowed_che_types.set([types['TT']])

    def asset(code, name, kind, geometry, parent=None, access_policy=None):
        return LayoutAsset.objects.create(
            layout_version=layout, code=code, name=name, asset_kind=kind,
            geometry=geometry, parent=parent, access_policy=access_policy,
            is_visible=True, is_active=True,
        )

    # Terminal-local meters: x east, y north; water is north of the quay face.
    terminal = asset('TERMINAL', 'Terminal boundary', LayoutAsset.TERMINAL, _rectangle(location, 0, 0, 400, 300))
    land = asset('LAND', 'Operational land', LayoutAsset.LAND, _rectangle(location, 0, 0, 400, 260), terminal)
    asset('WATER', 'Harbour water', LayoutAsset.WATER, _rectangle(location, 0, 260, 400, 40), terminal)

    road = asset('ROAD-01', 'Main road', LayoutAsset.ROAD, _rectangle(location, 20, 135, 360, 30), land, policy)
    road_axis = LinearAxis.objects.create(
        asset=road, code='ROAD-01.CENTER', name='Main road centerline',
        axis_kind=LinearAxis.ROAD_CENTERLINE,
        points=[_coordinate(location, x, 150) for x in (30, 140, 260, 370)],
        direction=LinearAxis.BOTH, lane_count=2, capacity=4, width_m=14,
        speed_limit_kph=30, access_policy=policy,
    )
    sync_axis_graph(road_axis)

    quay = asset('QUAY-01', 'Main quay', LayoutAsset.QUAY, _rectangle(location, 50, 225, 300, 35), land)
    quay_points = [_coordinate(location, 50, 260), _coordinate(location, 350, 260)]
    quay_axis = LinearAxis.objects.create(
        asset=quay, code='QUAY-01.FACE', name='Main quay face',
        axis_kind=LinearAxis.QUAY_FACE, points=quay_points,
        direction=LinearAxis.BOTH, lane_count=1, capacity=1,
    )
    sync_axis_graph(quay_axis)
    berth_length = geographic_distance_m(quay_points[0], quay_points[1])
    berth = BerthSegment.objects.create(
        quay_asset=quay, quay_face_axis=quay_axis, code='BERTH-01', name='Berth 01',
        start_chainage_m=0, end_chainage_m=berth_length,
        water_side=BerthSegment.WATER_LEFT, bollard_direction=BerthSegment.BOLLARDS_FORWARD,
    )
    sync_berth_bollards(berth, bollard_count=13, default_bollard_spacing_m=25)

    block = asset(
        EXCEPTION_BLOCK_NAME, f'Yard block {EXCEPTION_BLOCK_NAME}', LayoutAsset.YARD_BLOCK,
        _rectangle(location, 100, 172, 90, 30), land,
    )
    anchor = _coordinate(location, 100, 175)
    grid = StorageGrid.objects.create(
        layout_asset=block, name=EXCEPTION_BLOCK_NAME, grid_kind=StorageGrid.MANUAL,
        max_bays=10, max_rows=4, max_tier=3,
        longitude=anchor[0], latitude=anchor[1], heading_deg=0,
        bay_use_heading_axis=True, bay_naming=StorageGrid.NUMBERS,
        row_naming=StorageGrid.NUMBERS, bay_spacing_m=0.4, row_spacing_m=0.3,
    )
    sync_yard_grid(grid)

    block_lane = TransferInterface.objects.create(
        asset=block, code=f'{EXCEPTION_BLOCK_NAME}.TL', name=f'{EXCEPTION_BLOCK_NAME} truck transfer lane',
        storage_grid=grid, axis=road_axis, road_axis=road_axis, access_policy=policy,
        interface_kind=TransferInterface.VEHICLE_LANE, side=TransferInterface.LEFT,
        geometry=_line(location, (100, 166), (190, 166)),
        capacity=2, requires_vehicle_present=True,
    )
    sync_interface_positions(block_lane, 2)
    _handoff_edges(layout, road_axis, block_lane, policy)

    qc_lane = TransferInterface.objects.create(
        asset=quay, code='QC01.LANE', name='QC01 truck lane under the crane',
        axis=quay_axis, road_axis=road_axis, access_policy=policy,
        interface_kind=TransferInterface.VEHICLE_LANE, side=TransferInterface.LEFT,
        geometry=_line(location, (140, 240), (160, 240)),
        capacity=1, requires_vehicle_present=True,
    )
    sync_interface_positions(qc_lane, 1)
    _handoff_edges(layout, road_axis, qc_lane, policy)

    return {
        'layout': layout, 'policy': policy, 'road': road, 'road_axis': road_axis,
        'quay': quay, 'quay_axis': quay_axis, 'berth': berth, 'berth_length': berth_length,
        'block': block, 'grid': grid, 'block_lane': block_lane, 'qc_lane': qc_lane,
    }


def _seed_ches(location, types, site_layout):
    ches = {}
    for name, code, x_m, y_m, current_asset in [
        ('QC01', 'STS', 150, 250, site_layout['quay']),
        ('TT01', 'TT', 60, 150, site_layout['road']),
        ('TT02', 'TT', 80, 150, site_layout['road']),
        ('RTG01', 'RTG', 110, 185, site_layout['block']),
    ]:
        longitude, latitude = _coordinate(location, x_m, y_m)
        ches[name] = Che.objects.create(
            name=name, equipment_type=types[code], location=location,
            current_asset=current_asset, longitude=longitude, latitude=latitude,
            powered_on=False, working=False, is_available=False,
        )
    CheOperatingScope.objects.create(
        che=ches['QC01'], asset=site_layout['quay'], axis=site_layout['quay_axis'],
        min_chainage_m=25, max_chainage_m=site_layout['berth_length'] - 25, can_reposition=False,
    )
    for name in ('TT01', 'TT02'):
        CheOperatingScope.objects.create(
            che=ches[name], asset=site_layout['road'], axis=site_layout['road_axis'], can_reposition=True,
        )
    CheOperatingScope.objects.create(
        che=ches['RTG01'], asset=site_layout['block'], axis=None,
        bay_start=1, bay_end=10, row_start=1, row_end=4,
    )
    return ches


def _route_edges(layout, start, end):
    """Shortest path between two operational positions (empty if not connected)."""
    try:
        return _shortest_path(layout, _endpoint_node(start), _endpoint_node(end))
    except ValidationError:
        return []


def _seed_discharge(location, types, site_layout, rng, containers):
    layout = site_layout['layout']
    iso_type = ISOType.objects.create(iso_type='22G1', length=ISOType.L20, height=ISOType.HNC)
    owner = ContainerOwner.objects.create(name='Terminal Velocity Line', code=OWNER_CODE, is_shippingline=True)
    visit = CarrierVisit.objects.create(
        name=VISIT_NAME, visit_type=CarrierVisit.VESSEL, status=CarrierVisit.ARRIVED,
        location=location, vessel_name='TV PIONEER', voyage_number='001I',
        carrier='Terminal Velocity Line', berth='BERTH-01', planned_berth=site_layout['berth'],
        eta=SCENARIO_EPOCH, ata=SCENARIO_EPOCH, etd=SCENARIO_EPOCH + timedelta(hours=12),
        moves_in=containers, moves_import=containers, total_moves=containers,
        primary_equipment_type=types['STS'],
    )
    work_queue = WorkQueue.objects.create(
        name=WORK_QUEUE_NAME, description='QC01 discharge V001', is_active=True, location=location,
    )
    qc_position = site_layout['qc_lane'].positions.order_by('sequence').first().operational_position
    tl_position = site_layout['block_lane'].positions.order_by('sequence').first().operational_position

    # RNG draws, in a fixed order: serials, weights, then yard slots.
    serials = rng.sample(range(100000, 1000000), containers)
    weights = [rng.randrange(8000, 28001, 100) for _ in range(containers)]
    free_slots = list(
        Position.objects.filter(pile__storage_grid=site_layout['grid'], tier=1)
        .select_related('operational_position').order_by('pk')
    )
    yard_slots = rng.sample(free_slots, containers)

    orders = []
    for index in range(containers):
        tier = VESSEL_FIRST_TIER + 2 * index
        stow = f'{VESSEL_BAY:03d}{VESSEL_ROW:02d}{tier:02d}'
        vessel_slot = OperationalPosition.objects.create(
            layout_version=layout, code=f'{VISIT_NAME}.{stow}', name=f'{VISIT_NAME} slot {stow}',
            position_kind=OperationalPosition.VESSEL_SLOT, asset=site_layout['quay'],
            axis=site_layout['quay_axis'],
            latitude=_coordinate(location, 150, 268)[1], longitude=_coordinate(location, 150, 268)[0],
            logical_address={
                'carrier_visit': VISIT_NAME, 'bay': VESSEL_BAY, 'row': VESSEL_ROW,
                'tier': tier, 'stow': stow,
            },
        )
        unit = ContainerUnit.objects.create(
            container_number=container_number(OWNER_CODE, serials[index]),
            iso_type=iso_type, owner=owner, is_in_terminal=False,
        )
        container_visit = ContainerVisit.objects.create(
            category=ContainerVisit.IMPORT, container_number=unit, ib_carrier=visit,
            location=location, eta=SCENARIO_EPOCH.date(), weight=weights[index],
            status=ContainerVisit.PREADVISED,
            bill_of_lading=f'TVL{VISIT_NAME}{index + 1:03d}',
        )
        yard_slot = yard_slots[index].operational_position
        order = Order.objects.create(
            order_type=Order.MOVE_IN, status=Order.DRAFT, priority=10 + index,
            container_number=container_visit, work_queue=work_queue,
            from_position=vessel_slot, to_position=yard_slot,
            etc=SCENARIO_EPOCH + timedelta(hours=1, minutes=10 * index),
        )
        plan = OrderRoutePlan.objects.create(
            order=order, layout_version=layout, version=1, status=OrderRoutePlan.ACTIVE,
            reason='TCL-006 import_v1 seed (QC -> TT -> RTG hand-off chain)',
        )
        # D-015/TCL-003a hand-off shape: QC lands on the truck under the crane,
        # the truck drives to the block lane, the RTG stacks into the slot.
        legs = [
            (OrderLeg.HANDLE, types['STS'], vessel_slot, qc_position),
            (OrderLeg.MOVE, types['TT'], qc_position, tl_position),
            (OrderLeg.HANDLE, types['RTG'], tl_position, yard_slot),
        ]
        total_cost = 0.0
        for sequence, (kind, equipment_type, start, end) in enumerate(legs, start=1):
            edges = _route_edges(layout, start, end) if kind == OrderLeg.MOVE else []
            distance = sum(float(edge.distance_m or 0.0) for edge in edges)
            leg = OrderLeg.objects.create(
                order=order, route_plan=plan, sequence=sequence, leg_kind=kind,
                equipment_type=equipment_type, distance=distance,
                start_position=start, end_position=end,
            )
            for edge_sequence, edge in enumerate(edges, start=1):
                OrderLegRouteEdge.objects.create(order_leg=leg, route_edge=edge, sequence=edge_sequence)
            total_cost += sum(float(edge.base_cost or 0.0) for edge in edges)
        plan.cost = total_cost
        plan.save(update_fields=['cost'])
        yard_slots[index].is_reserved = True
        yard_slots[index].save(update_fields=['is_reserved'])
        order.status = Order.PLANNED
        order.save(update_fields=['status'])
        orders.append(order)
    return visit, orders


@transaction.atomic
def seed(rng=None, containers=1):
    """Create the import_v1 world on empty MiniTOS tables and publish the Site."""
    if not 1 <= int(containers) <= MAX_CONTAINERS:
        raise ValueError(f'import_v1 supports 1..{MAX_CONTAINERS} containers.')
    rng = rng or random.Random(DEFAULT_SEED)
    location = Location.objects.create(
        name=LOCATION_NAME, location_type=Location.TL,
        latitude=17.954711, longitude=-102.162543,
        map_origin_latitude=17.954711, map_origin_longitude=-102.162543,
        map_width_m=400, map_height_m=300, timezone='America/Mexico_City',
    )
    types = _seed_equipment_types()
    site = _seed_site(location)
    site_layout = _seed_layout(location, types)
    ches = _seed_ches(location, types, site_layout)
    visit, orders = _seed_discharge(location, types, site_layout, rng, int(containers))
    # Real publish path: validates the layout, writes the snapshot and the
    # scms.site.configuration.published.v1 outbox event for OES/Godot.
    publish_layout(site_layout['layout'])
    site.refresh_from_db()
    return {
        'site': site, 'layout': site_layout['layout'], 'visit': visit,
        'orders': orders, 'ches': ches,
    }


def release_discharge(order_id=None):
    """Step ``release-discharge``: release the next PLANNED discharge order."""
    orders = Order.objects.filter(
        work_queue__name=WORK_QUEUE_NAME, order_type=Order.MOVE_IN,
    )
    if order_id is not None:
        order = orders.filter(pk=order_id).first()
        if order is None:
            raise ValidationError(f'Order {order_id} is not an import_v1 discharge order.')
    else:
        order = orders.filter(status=Order.PLANNED).order_by('priority', 'pk').first()
        if order is None:
            raise ValidationError('No PLANNED import_v1 discharge order left; run scenario-reset.')
    return release_order(order.pk)


STEPS = {
    'release-discharge': release_discharge,
}
