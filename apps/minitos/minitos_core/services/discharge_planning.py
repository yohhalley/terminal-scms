"""TCL-010: simple discharge planning when a vessel visit goes InOperation.

When a vessel visit moves to ``InOperation`` (``CarrierVisit.save()``, so the
transition API, the REST API, the Django admin and the shell all trigger it),
MiniTOS creates one PLANNED discharge order per container still on the vessel:

    1 HANDLE  STS  vessel slot      -> QC lane position     (hand-off H1)
    2 MOVE    TT   QC lane position -> block lane position  (hand-off H2, road edges)
    3 HANDLE  RTG  block lane       -> random free yard slot

This is the shape the TCL-011 hand-off design (sec. 2) and OES dispatch expect:
legs 1-2 end on lane positions with no inventory (stack) position, so OES treats
them as hand-off boundaries (``dispatch.is_handoff_boundary``); only leg 3 ends
on a yard slot (``destination_inventory_position_id`` set in the release
payload).

Idempotent: a container that already has a live discharge (MOVE_IN) order is
adopted, not planned again. The import_v1 seed still pre-creates its orders
until TCL-010b, so on the demo path every order is adopted.

The yard slot is a random free slot of BLOCK01 (the block served by the
block lane), drawn from a seeded ``random.Random`` (D-016: the same scenario
seed and visit give the same slots on a rerun). TCL-012 fills ground (tier 1)
first, then stacks on higher tiers up to ``StorageGrid.max_tier`` without
floating containers. TCL-012 owns the block choice, what "free" means, the
stacking order and the seed: see ``services.yard_planning``. Tests can pass
their own ``rng``.
"""

import itertools
import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from minitos_core.models import (
    CarrierVisit,
    ContainerVisit,
    EquipmentType,
    OperationalPosition,
    Order,
    OrderLeg,
    OrderLegRouteEdge,
    OrderRoutePlan,
    TransferInterface,
    WorkQueue,
)
from minitos_core.services import yard_planning
from minitos_core.services.routing import _endpoint_node, _shortest_path

logger = logging.getLogger(__name__)

# Container visits that are still on the vessel and so need a discharge move.
DISCHARGE_CATEGORIES = (ContainerVisit.IMPORT, ContainerVisit.TRANSSHIPMENT)
ON_VESSEL_STATUSES = (ContainerVisit.PREADVISED, ContainerVisit.ARRIVED, ContainerVisit.ON_VESSEL)
# An existing order in one of these states is not adopted (it is over or dead).
NOT_ADOPTABLE_ORDER_STATUSES = (Order.CANCELLED, Order.FAILED)
PLAN_REASON = 'TCL-010 discharge planning at InOperation (QC -> TT -> RTG hand-off chain)'
FIRST_PRIORITY = 10

CODE_NO_LAYOUT = 'discharge_no_layout'
CODE_NO_QC_LANE = 'discharge_no_qc_lane'
CODE_NO_BLOCK_LANE = 'discharge_no_block_lane'
CODE_NO_EQUIPMENT_TYPE = 'discharge_no_equipment_type'
CODE_NO_WORK_QUEUE = 'discharge_no_work_queue'
CODE_NO_FREE_SLOT = 'discharge_no_free_slot'
CODE_NO_ROUTE = 'discharge_no_route'


class DischargePlanningError(ValidationError):
    """Discharge planning cannot run. Keyed on ``status`` so the admin form
    shows it next to the status field; ``planning_code`` is one of CODE_*."""

    def __init__(self, message, code):
        self.planning_code = code
        self.planning_message = message
        super().__init__({'status': ValidationError(message, code=code)})


def default_rng(visit):
    """Seeded RNG for the yard slot draw (D-016): TCL-012 seeds it from the
    scenario seed plus the visit id (TL decision on PR #34)."""
    return yard_planning.slot_rng(visit)


def discharge_container_visits(visit):
    return ContainerVisit.objects.filter(
        ib_carrier=visit, category__in=DISCHARGE_CATEGORIES, status__in=ON_VESSEL_STATUSES,
    ).select_related('container_number').order_by('pk')


def existing_discharge_order(container_visit):
    return Order.objects.filter(
        container_number=container_visit, order_type=Order.MOVE_IN,
    ).exclude(status__in=NOT_ADOPTABLE_ORDER_STATUSES).order_by('pk').first()


def _fail(visit, code, text):
    raise DischargePlanningError(
        f'Visit {visit.name} cannot go to InOperation: {text} (TCL-010 discharge planning).', code,
    )


def _layout(visit):
    layout = visit.location.layout_versions.filter(is_current=True).first() if visit.location_id else None
    if layout is None:
        _fail(visit, CODE_NO_LAYOUT, 'the terminal has no current layout')
    return layout


def _first_lane_position(interface):
    slot = interface.positions.order_by('sequence').select_related('operational_position').first()
    return getattr(slot, 'operational_position', None) if slot else None


def _qc_lane(visit, layout):
    lanes = TransferInterface.objects.filter(
        asset__layout_version=layout, interface_kind=TransferInterface.VEHICLE_LANE,
        storage_grid__isnull=True,
    ).order_by('code')
    berth = visit.planned_berth
    preferred = lanes.filter(asset=berth.quay_asset) if berth is not None else lanes.none()
    for interface in list(preferred) + [lane for lane in lanes if lane.asset.asset_kind == 'quay']:
        position = _first_lane_position(interface)
        if position is not None:
            return interface, position
    _fail(visit, CODE_NO_QC_LANE, 'no truck lane under a quay crane in the current layout')


def _block_lane(visit, layout):
    lanes = TransferInterface.objects.filter(
        asset__layout_version=layout, interface_kind=TransferInterface.VEHICLE_LANE,
        storage_grid__isnull=False,
    ).select_related('storage_grid').order_by('code')
    # TCL-012: BLOCK01 is the import block when the layout has it.
    for interface in yard_planning.prefer_import_block(lanes):
        position = _first_lane_position(interface)
        if position is not None:
            return interface, position
    _fail(visit, CODE_NO_BLOCK_LANE, 'no yard block truck transfer lane in the current layout')


def _equipment_types(visit):
    types = {}
    for code in (EquipmentType.STS, EquipmentType.TT, EquipmentType.RTG):
        equipment_type = EquipmentType.objects.filter(equipment_type=code).order_by('pk').first()
        if equipment_type is None:
            _fail(visit, CODE_NO_EQUIPMENT_TYPE, f'no {code} equipment type')
        types[code] = equipment_type
    return types


def _work_queue(visit):
    # PLACEHOLDER until a stowage import exists (post-MVP): takes the first
    # active work queue at the terminal. A real plan would pick the queue of
    # the crane assigned to the container's bay.
    queue = WorkQueue.objects.filter(location=visit.location, is_active=True).order_by('name').first()
    if queue is None:
        _fail(visit, CODE_NO_WORK_QUEUE, f'no active work queue at {visit.location}')
    return queue


def free_yard_slots(grid):
    """Unheld yard slots of ``grid`` that a plan can still reserve (TCL-012
    definition in ``services.yard_planning``; stacking order is applied by
    ``choose_slots``)."""
    return yard_planning.free_yard_slots(grid)


def _vessel_slot(visit, layout, qc_interface, qc_position, container_visit):
    """The container's vessel slot: one already in the layout for it (seeded or
    from a future stowage import), else a placeholder at the crane.

    PLACEHOLDER until a stowage import exists (post-MVP): there is no real
    bay/row/tier stow data yet, so the fallback slot sits at the QC lane
    position with ``stow: None``.
    """
    slot = OperationalPosition.objects.filter(
        layout_version=layout, position_kind=OperationalPosition.VESSEL_SLOT,
        logical_address__container_visit_id=container_visit.pk,
    ).first()
    if slot is not None:
        return slot
    code = f'{visit.name}.CV{container_visit.pk}'
    slot, _ = OperationalPosition.objects.get_or_create(
        layout_version=layout, code=code,
        defaults={
            'name': f'{visit.name} slot for {container_visit.container_number_id}',
            'position_kind': OperationalPosition.VESSEL_SLOT,
            'asset': qc_interface.asset, 'axis': qc_interface.axis,
            'latitude': qc_position.latitude, 'longitude': qc_position.longitude,
            'logical_address': {
                'carrier_visit': visit.name, 'container_visit_id': container_visit.pk,
                'stow': None,
            },
        },
    )
    return slot


def _road_edges(visit, layout, start, end):
    try:
        edges = _shortest_path(layout, _endpoint_node(start), _endpoint_node(end))
    except ValidationError as exc:
        _fail(visit, CODE_NO_ROUTE, f'no road route from {start.code} to {end.code}: {"; ".join(exc.messages)}')
    if not edges:
        _fail(visit, CODE_NO_ROUTE, f'no road route from {start.code} to {end.code}')
    return list(edges)


class PlannedDischarge:
    __slots__ = ('container_visit', 'created', 'order')

    def __init__(self, container_visit, order, created):
        self.container_visit = container_visit
        self.order = order
        self.created = created

    def __repr__(self):
        verb = 'created' if self.created else 'adopted'
        return f'<PlannedDischarge {self.container_visit.container_number_id} order {self.order.pk} {verb}>'


EXPECTED_LEG_SHAPE = (
    (1, OrderLeg.HANDLE, EquipmentType.STS),
    (2, OrderLeg.MOVE, EquipmentType.TT),
    (3, OrderLeg.HANDLE, EquipmentType.RTG),
)


def leg_plan_differences(order):
    """How ``order``'s legs differ from the 3-leg STS -> TT -> RTG plan, as a
    list of short strings (empty if it matches). Checks sequence, kind and
    equipment type, and the hand-off chain: each leg starts where the previous
    one ended, leg 1 starts at the order's origin, leg 3 ends at its target."""
    legs = list(order.order_legs.select_related('equipment_type').order_by('sequence'))
    diffs = []
    actual = [(leg.sequence, leg.leg_kind, getattr(leg.equipment_type, 'equipment_type', None)) for leg in legs]
    if actual != list(EXPECTED_LEG_SHAPE):
        diffs.append(f'legs {actual} != expected {list(EXPECTED_LEG_SHAPE)}')
    for previous, leg in itertools.pairwise(legs):
        if previous.end_position_id != leg.start_position_id:
            diffs.append(f'leg {leg.sequence} starts at {leg.start_position_id}, '
                         f'leg {previous.sequence} ends at {previous.end_position_id}')
    if legs and legs[0].start_position_id != order.from_position_id:
        diffs.append(f'leg 1 starts at {legs[0].start_position_id}, order from {order.from_position_id}')
    if legs and legs[-1].end_position_id != order.to_position_id:
        diffs.append(f'last leg ends at {legs[-1].end_position_id}, order to {order.to_position_id}')
    return diffs


def _warn_if_not_planned_shape(container_visit, order):
    diffs = leg_plan_differences(order)
    if diffs:
        logger.warning(
            'TCL-010 adopting order %s for container %s unchanged, but its legs do not match '
            'the 3-leg discharge plan: %s', order.pk, container_visit.container_number_id, '; '.join(diffs),
        )


def check_discharge_plannable(visit):
    """Raise DischargePlanningError if planning would fail; plan nothing.

    Called from CarrierVisit.clean() so the admin form refuses InOperation with
    a field error instead of failing inside save().
    """
    pending = [cv for cv in discharge_container_visits(visit) if existing_discharge_order(cv) is None]
    if not pending:
        return
    layout = _layout(visit)
    _qc_lane(visit, layout)
    block_interface, _ = _block_lane(visit, layout)
    _equipment_types(visit)
    _work_queue(visit)
    free = free_yard_slots(block_interface.storage_grid).count()
    if free < len(pending):
        _no_free_slot(visit, block_interface.storage_grid, len(pending), free)


def _no_free_slot(visit, grid, needed, free):
    _fail(
        visit, CODE_NO_FREE_SLOT,
        f'{needed} container(s) need a yard slot but block {grid.name} has {free} free slot(s)',
    )


@transaction.atomic
def plan_visit_discharge(visit, rng=None):
    """Create (or adopt) the discharge order of every container on ``visit``.

    Returns a list of PlannedDischarge, one per discharge container, in
    container-visit order. Raises DischargePlanningError (nothing is written)
    if any container cannot be planned.
    """
    visit = CarrierVisit.objects.select_for_update().get(pk=visit.pk)
    containers = list(discharge_container_visits(visit))
    results = []
    pending = []
    for container_visit in containers:
        order = existing_discharge_order(container_visit)
        if order is not None:
            # Adopt unchanged (no re-planning in S2, TL decision on PR #34).
            _warn_if_not_planned_shape(container_visit, order)
            results.append(PlannedDischarge(container_visit, order, created=False))
        else:
            pending.append(container_visit)
    if not pending:
        return results

    layout = _layout(visit)
    qc_interface, qc_position = _qc_lane(visit, layout)
    block_interface, tl_position = _block_lane(visit, layout)
    types = _equipment_types(visit)
    work_queue = _work_queue(visit)
    grid = block_interface.storage_grid
    free = list(free_yard_slots(grid).select_for_update(of=('self',)))
    if len(free) < len(pending):
        _no_free_slot(visit, grid, len(pending), len(free))
    rng = rng or default_rng(visit)
    slots = yard_planning.choose_slots(free, len(pending), rng)
    road = _road_edges(visit, layout, qc_position, tl_position)

    next_priority = FIRST_PRIORITY + Order.objects.filter(
        container_number__ib_carrier=visit, order_type=Order.MOVE_IN,
    ).count()
    created = {}
    for index, container_visit in enumerate(pending):
        slot = slots[index]
        yard_position = slot.operational_position
        vessel_slot = _vessel_slot(visit, layout, qc_interface, qc_position, container_visit)
        order = Order.objects.create(
            order_type=Order.MOVE_IN, status=Order.DRAFT, priority=min(next_priority + index, 999),
            container_number=container_visit, work_queue=work_queue,
            from_position=vessel_slot, to_position=yard_position,
        )
        plan = OrderRoutePlan.objects.create(
            order=order, layout_version=layout, version=1, status=OrderRoutePlan.ACTIVE,
            reason=PLAN_REASON, cost=sum(float(edge.base_cost or 0.0) for edge in road),
        )
        legs = [
            (OrderLeg.HANDLE, types[EquipmentType.STS], vessel_slot, qc_position, []),
            (OrderLeg.MOVE, types[EquipmentType.TT], qc_position, tl_position, road),
            (OrderLeg.HANDLE, types[EquipmentType.RTG], tl_position, yard_position, []),
        ]
        for sequence, (kind, equipment_type, start, end, edges) in enumerate(legs, start=1):
            leg = OrderLeg.objects.create(
                order=order, route_plan=plan, sequence=sequence, leg_kind=kind,
                equipment_type=equipment_type, start_position=start, end_position=end,
                distance=sum(float(edge.distance_m or 0.0) for edge in edges),
            )
            OrderLegRouteEdge.objects.bulk_create([
                OrderLegRouteEdge(order_leg=leg, route_edge=edge, sequence=edge_sequence)
                for edge_sequence, edge in enumerate(edges, start=1)
            ])
        slot.is_reserved = True
        slot.save(update_fields=['is_reserved'])
        order.status = Order.PLANNED
        order.save(update_fields=['status'])
        created[container_visit.pk] = order

    by_container = {result.container_visit.pk: result for result in results}
    ordered = []
    for container_visit in containers:
        if container_visit.pk in created:
            ordered.append(PlannedDischarge(container_visit, created[container_visit.pk], created=True))
        else:
            ordered.append(by_container[container_visit.pk])
    return ordered
