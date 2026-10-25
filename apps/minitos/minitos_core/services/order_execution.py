import uuid

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from minitos_core.models import (
    Che,
    ContainerVisit,
    EquipmentType,
    IntegrationOutbox,
    Order,
    OrderLeg,
    OrderRoutePlan,
    Position,
)
from minitos_core.services.layout import resolved_interface_point
from minitos_core.services.site_sync import ensure_site_for_location


EXECUTION_EVENTS = {
    'order_started',
    'leg_dispatched',
    'leg_started',
    'container_picked_up',
    'container_dropped',
    'leg_completed',
    'leg_failed',
    'leg_blocked',
    'order_failed',
    'order_cancelled',
    'container_transferred',
    'handoff_exception',
}

QUAY_CRANE_TYPES = {'STS', 'QC'}
VESSEL_SOURCE = 'VESSEL'
EXCEPTION_BLOCK_NAME = 'BLOCK01'


# Assumption A-1 (TL interim call, pending Yael): a TT no-show completes the
# order *by exception*; the visit carries the exception reason. Flip to
# Order.CANCELLED here (and NO_SHOW_ORDER_STATUS in OES handoffs.py) if Yael
# decides no-show cancels the order.
NO_SHOW_ORDER_STATUS = Order.COMPLETE


def _check_container_identity(order, details):
    """QA/TL: an event naming a different container than the order is rejected."""
    visit_id = details.get('container_visit_id')
    number = details.get('container_number')
    if visit_id is None and not number:
        return
    expected_visit = order.container_number_id
    expected_number = (
        order.container_number.container_number_id if order.container_number_id else None
    )
    if visit_id is not None and (expected_visit is None or int(visit_id) != expected_visit):
        raise ValidationError(
            f'container_number_mismatch: event container_visit_id {visit_id} '
            f'does not match order container visit {expected_visit}.',
            code='container_number_mismatch',
        )
    if number and str(number).strip().upper() != str(expected_number or '').upper():
        raise ValidationError(
            f'container_number_mismatch: event container_number {number} '
            f'does not match order container {expected_number}.',
            code='container_number_mismatch',
        )


def _event_time(value=None):
    return value or timezone.now()


def _active_legs(order):
    return order.order_legs.filter(route_plan__status=OrderRoutePlan.ACTIVE)


def _terminal_code(location_id):
    return f'terminal-{location_id}'


def _leg_movement_mode(leg):
    modes = list(
        leg.route_edges.values_list('route_edge__movement_mode', flat=True).distinct()
    )
    if len(modes) == 1:
        return modes[0]
    return 'multi' if modes else ''


def _position_coordinates(position):
    if position is None:
        return None, None
    if position.interface_position_id:
        point = resolved_interface_point(position.interface_position)
        if point:
            return point[1], point[0]
    return position.latitude, position.longitude


def _queue_released_order(order):
    location = order.work_queue.location
    site, _ = ensure_site_for_location(location)
    site = type(site).objects.select_related('enterprise', 'location').get(pk=site.pk)
    terminal_code = _terminal_code(location.pk)
    logical_event_id = uuid.uuid4()
    legs = []
    for leg in _active_legs(order).select_related(
        'start_position__stack_position',
        'end_position__stack_position',
    ).prefetch_related('route_edges__route_edge'):
        source_latitude, source_longitude = _position_coordinates(leg.start_position)
        destination_latitude, destination_longitude = _position_coordinates(leg.end_position)
        legs.append({
            'id': leg.pk,
            'sequence': leg.sequence,
            'leg_kind': leg.leg_kind,
            'status': leg.status,
            'equipment_type': leg.equipment_type_id,
            'movement_mode': _leg_movement_mode(leg),
            'start_position': leg.start_position_id,
            'end_position': leg.end_position_id,
            'source_inventory_position_id': (
                leg.start_position.stack_position_id if leg.start_position_id else None
            ),
            'destination_inventory_position_id': (
                leg.end_position.stack_position_id if leg.end_position_id else None
            ),
            # Contract rev 9.3 / O6: readable destination code for Godot's
            # Command/DestinationPositionCode.
            'destination_position_code': (
                leg.end_position.code if leg.end_position_id else None
            ),
            'source_latitude': source_latitude,
            'source_longitude': source_longitude,
            'destination_latitude': destination_latitude,
            'destination_longitude': destination_longitude,
            'planned_start': leg.planned_start.isoformat() if leg.planned_start else None,
            'planned_end': leg.planned_end.isoformat() if leg.planned_end else None,
        })
    payload = {
        'schema': 'scms.order.released.v1',
        'event_id': str(logical_event_id),
        'occurred_at': order.released_at.isoformat(),
        'enterprise': {
            'id': str(site.enterprise_id),
            'code': site.enterprise.code,
            'name': site.enterprise.name,
        },
        'site': {
            'id': str(site.pk),
            'code': site.code,
            'name': site.location.name,
            'source_location_id': location.pk,
            'location_type': location.location_type,
            'configuration_revision': site.configuration_revision,
        },
        'terminal': {'id': location.pk, 'code': terminal_code},
        'data': {
            'order': {
                'id': order.pk,
                'status': order.status,
                'priority': order.priority,
                'container_number': order.container_number_id,
                # D-010: the ISO 6346 number itself ('container_number' is the visit pk).
                'container_unit_number': (
                    order.container_number.container_number_id
                    if order.container_number_id else None
                ),
                'from_position': order.from_position_id,
                'to_position': order.to_position_id,
                'released_at': order.released_at.isoformat(),
            },
            'legs': legs,
        },
    }
    event = IntegrationOutbox.objects.create(
        event_id=logical_event_id,
        topic=(
            f'{settings.SITE_UNS_ROOT}/{site.enterprise.code}/{site.code}'
            f'/tos/orders/{order.pk}/released'
        ),
        schema='scms.order.released.v1',
        payload=payload,
        qos=1,
    )
    if settings.UNS_DUAL_PUBLISH_LEGACY:
        IntegrationOutbox.objects.create(
            topic=f'{settings.UNS_ROOT}/{terminal_code}/tos/orders/{order.pk}/released',
            schema=event.schema,
            payload=payload,
            qos=1,
        )
    return event


@transaction.atomic
def release_order(order_id, released_at=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status == Order.RELEASED:
        return order
    if order.status != Order.PLANNED:
        raise ValidationError('Only a planned order can be released.')
    if not order.route_plans.filter(status=OrderRoutePlan.ACTIVE).exists():
        raise ValidationError('The order requires an active route plan before release.')
    if not _active_legs(order).exists():
        raise ValidationError('The active route plan contains no executable legs.')
    order.status = Order.RELEASED
    order.released_at = _event_time(released_at)
    order.save(update_fields=['status', 'released_at'])
    _queue_released_order(order)
    return order


def _locked_che(che_id, location_id):
    try:
        return Che.objects.select_for_update().get(pk=che_id, location_id=location_id)
    except Che.DoesNotExist as exc:
        raise ValidationError('The CHE does not belong to the order terminal.') from exc


@transaction.atomic
def pickup_container(container_visit_id, che_id):
    visit = ContainerVisit.objects.select_for_update().get(pk=container_visit_id)
    che = _locked_che(che_id, visit.location_id)
    if visit.status == ContainerVisit.ON_EQUIPMENT and visit.current_che_id == che.pk:
        return visit
    if visit.current_che_id:
        raise ValidationError('The container is already carried by another CHE.')

    position = Position.objects.select_for_update().filter(container_visit=visit).first()
    if not position:
        raise ValidationError('The container is not assigned to a yard position.')

    position.container_visit = None
    position.save(update_fields=['container_visit', 'is_occupied'])
    visit.current_che = che
    visit.status = ContainerVisit.ON_EQUIPMENT
    visit.latitude = che.latitude
    visit.longitude = che.longitude
    visit.save(update_fields=[
        'current_che', 'status', 'latitude', 'longitude', 'last_updated',
    ])
    return visit


def _is_quay_crane(che):
    equipment_type = che.equipment_type
    return (
        equipment_type.equipment_type == EquipmentType.STS
        or equipment_type.name.upper() in QUAY_CRANE_TYPES
    )


@transaction.atomic
def place_container_on_quay_crane(container_visit_id, che_id):
    """D-012/T-007 (B2): the container appears on the QC; custody On Equipment / QC (D-015).

    There is no lift from the vessel to model. Idempotent for the same QC.
    """
    visit = ContainerVisit.objects.select_for_update().get(pk=container_visit_id)
    che = _locked_che(che_id, visit.location_id)
    if visit.status == ContainerVisit.ON_EQUIPMENT and visit.current_che_id == che.pk:
        return visit
    if not _is_quay_crane(che):
        raise ValidationError('Only a quay crane can take a container from the vessel.')
    if visit.current_che_id:
        raise ValidationError('The container is already carried by another CHE.')
    if Position.objects.filter(container_visit=visit).exists():
        raise ValidationError('The container is in the yard, not on the vessel.')
    if visit.status in {ContainerVisit.DEPARTED, ContainerVisit.COMPLETED}:
        raise ValidationError('The container visit is closed.')
    visit.current_che = che
    visit.status = ContainerVisit.ON_EQUIPMENT
    visit.latitude = che.latitude
    visit.longitude = che.longitude
    visit.save(update_fields=[
        'current_che', 'status', 'latitude', 'longitude', 'last_updated',
    ])
    return visit


@transaction.atomic
def transfer_container(container_visit_id, from_che_id, to_che_id):
    """T-006: one CHE-to-CHE hand-off recorded as one custody event. Idempotent."""
    visit = ContainerVisit.objects.select_for_update().get(pk=container_visit_id)
    from_che = _locked_che(from_che_id, visit.location_id)
    to_che = _locked_che(to_che_id, visit.location_id)
    if visit.status == ContainerVisit.ON_EQUIPMENT and visit.current_che_id == to_che.pk:
        return visit
    if visit.status != ContainerVisit.ON_EQUIPMENT or visit.current_che_id != from_che.pk:
        raise ValidationError('The container is not carried by the giving CHE.')
    visit.current_che = to_che
    visit.latitude = to_che.latitude
    visit.longitude = to_che.longitude
    visit.save(update_fields=['current_che', 'latitude', 'longitude', 'last_updated'])
    return visit


def _exception_slot(visit, preferred_position_id=None):
    """A free ground slot in BLOCK01 (D-009 dummy slots); the planned slot first if free.

    N-5: only BLOCK01 slots qualify, also for the planned (preferred) slot.
    """
    base = Position.objects.select_for_update().filter(
        pile__storage_grid__layout_asset__layout_version__location_id=visit.location_id,
        pile__storage_grid__name=EXCEPTION_BLOCK_NAME,
        container_visit__isnull=True,
    )
    if preferred_position_id:
        preferred = base.filter(pk=preferred_position_id).first()
        if preferred is not None and (
            preferred.tier == 1
            or not preferred.pile.positions.filter(
                tier__lt=preferred.tier, container_visit__isnull=True,
            ).exists()
        ):
            return preferred
    slot = base.filter(
        pile__storage_grid__name=EXCEPTION_BLOCK_NAME, tier=1,
    ).order_by('pile_id', 'pk').first()
    if slot is None:
        raise ValidationError(f'No free slot in {EXCEPTION_BLOCK_NAME} for the exception move.')
    return slot


@transaction.atomic
def relocate_container_after_exception(container_visit_id, reason, preferred_position_id=None):
    """D-013/T-007: put the container straight into BLOCK01, flagged as an exception move.

    Idempotent: a visit already relocated for an exception is returned as is.
    """
    visit = ContainerVisit.objects.select_for_update().get(pk=container_visit_id)
    current = Position.objects.filter(container_visit=visit).first()
    if current is not None and visit.exception_reason:
        return visit
    if current is not None:
        raise ValidationError('The container is already in the yard.')
    target = _exception_slot(visit, preferred_position_id)
    target.container_visit = visit
    target.save(update_fields=['container_visit', 'is_occupied'])
    visit.current_che = None
    visit.status = ContainerVisit.AT_YARD
    visit.exception_reason = str(reason)[:40]
    if hasattr(target, 'operational_position'):
        visit.latitude = target.operational_position.latitude
        visit.longitude = target.operational_position.longitude
    visit.save(update_fields=[
        'current_che', 'status', 'exception_reason', 'latitude', 'longitude', 'last_updated',
    ])
    if visit.container_number_id and not visit.container_number.is_in_terminal:
        visit.container_number.is_in_terminal = True
        visit.container_number.save(update_fields=['is_in_terminal'])
    return visit


@transaction.atomic
def drop_container(container_visit_id, che_id, position_id):
    visit = ContainerVisit.objects.select_for_update().get(pk=container_visit_id)
    che = _locked_che(che_id, visit.location_id)
    target = Position.objects.select_for_update().get(pk=position_id)

    target_location_id = target.pile.storage_grid.layout_asset.layout_version.location_id
    if target_location_id != visit.location_id:
        raise ValidationError('The target position belongs to another terminal.')
    if target.container_visit_id == visit.pk and visit.current_che_id is None:
        return visit
    if visit.status != ContainerVisit.ON_EQUIPMENT or visit.current_che_id != che.pk:
        raise ValidationError('The container is not currently carried by this CHE.')
    if target.container_visit_id and target.container_visit_id != visit.pk:
        raise ValidationError('The target position is already occupied.')

    target.container_visit = visit
    target.save(update_fields=['container_visit', 'is_occupied'])
    visit.current_che = None
    visit.status = ContainerVisit.AT_YARD
    if hasattr(target, 'operational_position'):
        visit.latitude = target.operational_position.latitude
        visit.longitude = target.operational_position.longitude
    visit.save(update_fields=[
        'current_che', 'status', 'latitude', 'longitude', 'last_updated',
    ])
    if visit.container_number_id and not visit.container_number.is_in_terminal:
        visit.container_number.is_in_terminal = True
        visit.container_number.save(update_fields=['is_in_terminal'])
    return visit


def _set_che_execution_state(che, *, dispatched=None, working=None, available=None):
    update_fields = []
    for field, value in (
        ('is_dispatched', dispatched),
        ('working', working),
        ('is_available', available),
    ):
        if value is not None and getattr(che, field) != value:
            setattr(che, field, value)
            update_fields.append(field)
    if update_fields:
        update_fields.append('state_updated_at')
        che.save(update_fields=update_fields)


@transaction.atomic
def apply_execution_event(
    order_id,
    event,
    *,
    leg_id=None,
    che_id=None,
    position_id=None,
    occurred_at=None,
    details=None,
):
    details = details or {}
    if event not in EXECUTION_EVENTS:
        raise ValidationError(f'Unsupported execution event: {event}.')

    occurred_at = _event_time(occurred_at)
    order = Order.objects.select_for_update().get(pk=order_id)
    _check_container_identity(order, details)
    terminal_id = order.work_queue.location_id
    leg = None
    if leg_id is not None:
        try:
            leg = _active_legs(order).select_for_update().get(pk=leg_id)
        except OrderLeg.DoesNotExist as exc:
            raise ValidationError('The leg does not belong to the active order route.') from exc

    che = None
    resolved_che_id = che_id or (leg.che_id if leg else None)
    if resolved_che_id:
        che = _locked_che(resolved_che_id, terminal_id)

    if event == 'order_started':
        if order.status not in {Order.RELEASED, Order.IN_PROGRESS}:
            raise ValidationError('Only a released order can start execution.')
        order.status = Order.IN_PROGRESS
        order.save(update_fields=['status'])
    elif event == 'leg_dispatched':
        if not leg or not che:
            raise ValidationError('leg_dispatched requires leg_id and che_id.')
        leg.che = che
        leg.status = OrderLeg.DISPATCHED
        leg.save(update_fields=['che', 'status'])
        _set_che_execution_state(che, dispatched=True, working=False, available=False)
    elif event == 'leg_started':
        if not leg or not che:
            raise ValidationError('leg_started requires a dispatched leg and CHE.')
        leg.che = che
        leg.status = OrderLeg.WORKING
        leg.actual_start = leg.actual_start or occurred_at
        leg.save(update_fields=['che', 'status', 'actual_start'])
        order.status = Order.IN_PROGRESS
        order.save(update_fields=['status'])
        _set_che_execution_state(che, dispatched=True, working=True, available=False)
    elif event == 'container_picked_up':
        if not order.container_number_id or not che:
            raise ValidationError('container_picked_up requires an order container and CHE.')
        if str(details.get('source') or '').upper() == VESSEL_SOURCE:
            place_container_on_quay_crane(order.container_number_id, che.pk)
        else:
            pickup_container(order.container_number_id, che.pk)
    elif event == 'container_dropped':
        if not order.container_number_id or not che or position_id is None:
            raise ValidationError('container_dropped requires a container, CHE, and position_id.')
        drop_container(order.container_number_id, che.pk, position_id)
    elif event == 'container_transferred':
        from_che_id = details.get('from_che_id')
        to_che_id = details.get('to_che_id')
        if not order.container_number_id or not from_che_id or not to_che_id:
            raise ValidationError(
                'container_transferred requires a container, from_che_id and to_che_id.'
            )
        transfer_container(order.container_number_id, int(from_che_id), int(to_che_id))
    elif event == 'handoff_exception':
        if not order.container_number_id:
            raise ValidationError('handoff_exception requires an order container.')
        reason = details.get('reason')
        if not reason:
            # The schema requires reason on handoff_exception; no silent default.
            raise ValidationError('handoff_exception requires reason.', code='reason_required')
        relocate_container_after_exception(
            order.container_number_id, reason, details.get('destination_position_id'),
        )
        if order.status not in {Order.COMPLETE, Order.CANCELLED}:
            # A-1 (pending Yael): completed by exception; N-6: event time.
            order.status = NO_SHOW_ORDER_STATUS
            order.completed_at = _event_time(occurred_at)
            update_fields = ['status', 'completed_at']
            if order.status == Order.COMPLETE:
                order.atc = order.completed_at
                update_fields.append('atc')
            order.save(update_fields=update_fields)
        _active_legs(order).exclude(
            status__in=[OrderLeg.COMPLETE, OrderLeg.CANCELLED],
        ).update(status=OrderLeg.CANCELLED, actual_end=occurred_at)
        for released_che_id in (details.get('from_che_id'), details.get('to_che_id')):
            if released_che_id:
                released = _locked_che(int(released_che_id), terminal_id)
                _set_che_execution_state(
                    released, dispatched=False, working=False, available=True,
                )
    elif event == 'leg_completed':
        if not leg:
            raise ValidationError('leg_completed requires leg_id.')
        leg.status = OrderLeg.COMPLETE
        leg.actual_end = occurred_at
        leg.save(update_fields=['status', 'actual_end'])
        if che:
            _set_che_execution_state(che, dispatched=False, working=False, available=True)
        if not _active_legs(order).exclude(status=OrderLeg.COMPLETE).exists():
            order.status = Order.COMPLETE
            order.completed_at = occurred_at
            order.atc = occurred_at
            order.save(update_fields=['status', 'completed_at', 'atc'])
    elif event == 'leg_failed':
        if not leg:
            raise ValidationError('leg_failed requires leg_id.')
        leg.status = OrderLeg.FAILED
        leg.actual_end = occurred_at
        leg.save(update_fields=['status', 'actual_end'])
        order.status = Order.FAILED
        order.completed_at = occurred_at
        order.save(update_fields=['status', 'completed_at'])
        if che:
            _set_che_execution_state(che, dispatched=False, working=False, available=False)
    elif event == 'leg_blocked':
        if not leg:
            raise ValidationError('leg_blocked requires leg_id.')
        leg.status = OrderLeg.BLOCKED
        leg.save(update_fields=['status'])
        if che:
            _set_che_execution_state(che, dispatched=False, working=False, available=True)
    elif event == 'order_failed':
        order.status = Order.FAILED
        order.completed_at = occurred_at
        order.save(update_fields=['status', 'completed_at'])
    elif event == 'order_cancelled':
        order.status = Order.CANCELLED
        order.completed_at = occurred_at
        order.save(update_fields=['status', 'completed_at'])
        _active_legs(order).exclude(status=OrderLeg.COMPLETE).update(status=OrderLeg.CANCELLED)
        if che:
            _set_che_execution_state(che, dispatched=False, working=False, available=True)

    order.refresh_from_db()
    return order
