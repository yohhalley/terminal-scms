"""TCL-006 ``import_v1``: one vessel discharge into one yard block.

Seeds the smallest terminal the Sprint 1 demo needs (see the Sprint 1 note,
demo steps 1-9):

* Location "Test Terminal", Enterprise ``terminal-velocity``, Site ``test-terminal``;
* one quay (QUAY-01 / BERTH-01 with bollards), one yard block (BLOCK01, the
  no-show exception block), one road (ROAD-01) and two truck hand-off lanes
  (QC01 lane under the crane, BLOCK01 transfer lane);
* CHEs QC01 (STS), TT01, TT02 (TT) and RTG01 (RTG);
* vessel visit V001 (Arrived) with ``containers`` (default 1, max 5)
  pre-advised 20' import containers, each with its vessel slot.

TCL-010b (D-048, TCL-011 design sec. 8 (a)): the seed creates **no** discharge
orders. Step ``visit-in-operation`` moves V001 to InOperation, and TCL-010
discharge planning creates one PLANNED order (QC -> TT -> RTG legs) per
container, with the yard slot chosen by planning. ``release-discharge`` then
releases them.

Everything is deterministic: auto-increment ids restart on reset, Enterprise
and Site ids are uuid5 under ``base.SCENARIO_NAMESPACE``, and the seed's only
"random" choices (container serials, weights) come from
``random.Random(seed)``. Yard slots are drawn by discharge planning with its
own seeded RNG (D-016).
"""

import random
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import (
    AccessPolicy, BerthSegment, CarrierVisit, Che, CheOperatingScope,
    ContainerOwner, ContainerUnit, ContainerVisit, Enterprise, EquipmentType,
    ISOType, LayoutAsset, LinearAxis, Location, OperationalPosition, Order,
    RouteEdge, RouteNode, Site, StorageGrid, TransferInterface, WorkQueue,
)
from ..services import (
    current_layout, geographic_distance_m, local_to_geographic, publish_layout,
    sync_axis_graph, sync_berth_bollards, sync_interface_positions, sync_yard_grid,
)
from ..services.discharge_planning import plan_visit_discharge
from ..services.order_execution import EXCEPTION_BLOCK_NAME, release_order
from ..services.routing import _endpoint_node, _shortest_path
from ..services.site_sync import (
    DEFAULT_ENTERPRISE_CODE, DEFAULT_ENTERPRISE_NAME, _emit_site_created,
)
from .base import container_number, scenario_uuid

NAME = 'import_v1'
# K7 (docs/scenarios/import_v1.md): SIMULATOR_SEED=1. `make scenario-reset SEED=n`
# passes the same n to this seed and to the simulator (OES SIMULATOR_SEED).
DEFAULT_SEED = 1
MAX_CONTAINERS = 5

LOCATION_NAME = 'Test Terminal'
SITE_CODE = 'test-terminal'
VISIT_NAME = 'V001'
OWNER_CODE = 'MSCU'  # Check digits are real ISO 6346.
# The first container is the demo fixture MSCU1234566 (docs/scenarios/import_v1.md
# K5, S1 demo checklist); extra containers get seeded RNG serials.
FIRST_SERIAL = 123456
WORK_QUEUE_NAME = 'QC01-WQ'
# Scenario clock = the simulator clock origin (K7): SIMULATOR_START_EPOCH_MS
# 1792886400000 is 2026-10-25 00:00 UTC (compose.yaml default). Business times
# are constants on that clock, so a rerun produces the same visit/order data and
# the vessel's ATA is sim time 0.
SIMULATOR_START_EPOCH_MS = 1792886400000
SCENARIO_EPOCH = datetime.fromtimestamp(SIMULATOR_START_EPOCH_MS / 1000, tz=dt_timezone.utc)
# Vessel stowage for container n: bay 033, row 06, tier 82 + 2n (ISO 9711,
# BBBRRTT). The seeded boxes are 20' (22G1), and 20' boxes stow in odd bays;
# even bays denote 40' slots (SME review of #26, BL-26b).
VESSEL_BAY, VESSEL_ROW, VESSEL_FIRST_TIER = 33, 6, 82


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

    def asset(code, name, kind, geometry, parent=None, access_policy=None, heading_deg=0.0):
        return LayoutAsset.objects.create(
            layout_version=layout, code=code, name=name, asset_kind=kind,
            geometry=geometry, parent=parent, access_policy=access_policy,
            heading_deg=heading_deg, is_visible=True, is_active=True,
        )

    # Terminal-local meters: x east, y north; water is north of the quay face.
    # Quays and yard blocks follow the terminal editor's convention
    # (terminal_editor_map_tools.js): the polygon starts at the anchor and its
    # first edge runs along the heading (quay) or the bay axis (yard block).
    # Otherwise the editor draws the grid, water and lanes 90° off the polygon.
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

    # Anchor on the quay face at x=50, heading east (90°), water on the left
    # (north), land to the right (south), as the editor's quay footprint.
    quay_corners = [_coordinate(location, x, y) for x, y in ((50, 260), (350, 260), (350, 225), (50, 225))]
    quay = asset(
        'QUAY-01', 'Main quay', LayoutAsset.QUAY,
        {'type': 'Polygon', 'coordinates': [[*quay_corners, quay_corners[0]]]}, land, heading_deg=90.0,
    )
    quay_points = quay_corners[:2]
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
        # Heading north, bays along the east axis: the 10 bays run along the
        # 90 m side of the polygon and the 4 rows across its 30 m side.
        bay_use_heading_axis=False, bay_naming=StorageGrid.NUMBERS,
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
            # K3: every CHE starts powered on, available and not dispatched.
            powered_on=True, working=False, is_available=True, is_dispatched=False,
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
    """Shortest path between two operational positions.

    BE review S-2: an unroutable TT leg must fail the seed (and so roll back
    the whole reset) instead of silently seeding a leg with no edges.
    """
    try:
        edges = _shortest_path(layout, _endpoint_node(start), _endpoint_node(end))
    except ValidationError as exc:
        raise ValidationError(
            f'import_v1: no road route from {start.code} to {end.code}: {"; ".join(exc.messages)}'
        ) from exc
    if not edges:
        raise ValidationError(f'import_v1: no road route from {start.code} to {end.code}.')
    return list(edges)


def _seed_discharge(location, types, site_layout, rng, containers):
    """Seed the vessel visit (Arrived) and its pre-advised import containers.

    TCL-010b / D-048: no discharge orders, legs or yard reservations here. The
    single creation path is TCL-010 discharge planning when the visit goes
    InOperation (step ``visit-in-operation``), which picks the yard slot
    (TCL-012 rules). The seed only supplies what planning reads: the visit,
    the container visits, the QC work queue and each container's vessel slot.
    """
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
    WorkQueue.objects.create(
        name=WORK_QUEUE_NAME, description='QC01 discharge V001', is_active=True, location=location,
    )
    qc_position = site_layout['qc_lane'].positions.order_by('sequence').first().operational_position
    tl_position = site_layout['block_lane'].positions.order_by('sequence').first().operational_position
    # BE review S-2, kept after TCL-010b: a layout where the TT cannot drive
    # from the QC lane to the block lane fails the reset (and rolls it back)
    # instead of leaving a world that refuses InOperation later.
    _route_edges(layout, qc_position, tl_position)

    # RNG draws, in a fixed order: serials, then weights. Serials for
    # containers 2..n skip FIRST_SERIAL so numbers stay unique. Yard slots are
    # no longer drawn here (planning draws them at InOperation).
    serials = [FIRST_SERIAL] + [
        serial + 1 if serial >= FIRST_SERIAL else serial
        for serial in rng.sample(range(100000, 999999), containers - 1)
    ]
    weights = [rng.randrange(8000, 28001, 100) for _ in range(containers)]

    container_visits = []
    for index in range(containers):
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
        tier = VESSEL_FIRST_TIER + 2 * index
        stow = f'{VESSEL_BAY:03d}{VESSEL_ROW:02d}{tier:02d}'
        # ``container_visit_id`` lets discharge planning use this real BBBRRTT
        # slot (D-041) as the order origin instead of its CV<id> placeholder.
        OperationalPosition.objects.create(
            layout_version=layout, code=f'{VISIT_NAME}.{stow}', name=f'{VISIT_NAME} slot {stow}',
            position_kind=OperationalPosition.VESSEL_SLOT, asset=site_layout['quay'],
            axis=site_layout['quay_axis'],
            latitude=_coordinate(location, 150, 268)[1], longitude=_coordinate(location, 150, 268)[0],
            logical_address={
                'carrier_visit': VISIT_NAME, 'container_visit_id': container_visit.pk,
                'bay': VESSEL_BAY, 'row': VESSEL_ROW, 'tier': tier, 'stow': stow,
            },
        )
        container_visits.append(container_visit)
    return visit, container_visits


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
    visit, container_visits = _seed_discharge(location, types, site_layout, rng, int(containers))
    # Real publish path: validates the layout, writes the snapshot and the
    # scms.site.configuration.published.v1 outbox event for OES/Godot.
    publish_layout(site_layout['layout'])
    site.refresh_from_db()
    return {
        'site': site, 'layout': site_layout['layout'], 'visit': visit,
        'container_visits': container_visits, 'ches': ches,
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
            if not orders.exists():
                # TCL-010b: the seed creates no orders; planning does, at InOperation.
                raise ValidationError(
                    'No import_v1 discharge order yet; run the visit-in-operation step first.'
                )
            raise ValidationError('No PLANNED import_v1 discharge order left; run scenario-reset.')
    return release_order(order.pk)


class VisitInOperationResult:
    """Outcome of the ``visit-in-operation`` step, printed by scenario_step."""

    def __init__(self, visit, old_status, planned):
        self.visit = visit
        self.old_status = old_status
        self.planned = planned

    def summary(self):
        created = sum(1 for item in self.planned if item.created)
        adopted = len(self.planned) - created
        orders = ', '.join(str(item.order.pk) for item in self.planned) or 'none'
        return (
            f'visit {self.visit.name} {self.old_status} -> {self.visit.status}; '
            f'discharge orders {orders} ({created} created, {adopted} adopted).'
        )


def visit_in_operation(order_id=None):
    """Step ``visit-in-operation`` (TCL-010, design sec. 8 (a)): move the seeded
    visit Arrived -> InOperation through CarrierVisit.save(), the same path as
    the API and the admin, so discharge planning runs and creates the orders
    (the seed has none since TCL-010b). A rerun saves nothing (the visit is
    already InOperation) and planning is idempotent, so it only reports
    (adopts) the orders that exist."""
    if order_id is not None:
        raise ValidationError('visit-in-operation takes no --order.')
    with transaction.atomic():
        visit = CarrierVisit.objects.select_for_update().filter(name=VISIT_NAME).first()
        if visit is None:
            raise ValidationError(f'No {VISIT_NAME} visit; run scenario-reset.')
        old_status = visit.status
        # TCL-010b: CarrierVisit.save() already plans on Arrived -> InOperation,
        # so the plan_visit_discharge() call below only adopts. Remember which
        # orders existed before the save to report created vs adopted honestly.
        existing = set(
            Order.objects.filter(container_number__ib_carrier=visit, order_type=Order.MOVE_IN)
            .values_list('pk', flat=True)
        )
        if old_status != CarrierVisit.IN_OPERATION:
            visit.status = CarrierVisit.IN_OPERATION
            visit.save()
        planned = plan_visit_discharge(visit)
        for item in planned:
            item.created = item.order.pk not in existing
    return VisitInOperationResult(visit, old_status, planned)


STEPS = {
    'visit-in-operation': visit_in_operation,
    'release-discharge': release_discharge,
}
