"""TCL-012 (#10): random free yard slot per discharge order, seeded (D-016).

Called by TCL-010 discharge planning (``services.discharge_planning``) when a
vessel visit goes InOperation. This module owns three things:

Which block
    The MVP import block is ``BLOCK01`` (``IMPORT_BLOCK_NAME``, the same block
    as the D-013 exception block). If the current layout has a truck transfer
    lane serving a grid named BLOCK01, that block is used, even when it is
    full; a full BLOCK01 is an error, never a silent spill into another block.
    Only a layout with no BLOCK01 lane at all keeps TCL-010's rule (first
    block lane by code). Note (``prefer_import_block``): when any BLOCK01 lane
    exists only BLOCK01 lanes are returned, so if BLOCK01 has a transfer
    interface but no usable lane position, planning fails with
    ``discharge_no_block_lane`` rather than using another block's lane (by
    design: no silent spill).

What "free" means (``free_yard_slots``)
    A slot of the block's grid at tier 1..``StorageGrid.max_tier`` (the layout
    source of truth for stack height; import_v1's BLOCK01 is max_tier=3), with
    an operational position (so an order can point at it), that is

    * not occupied: no ``container_visit`` and ``is_occupied`` is False;
    * not reserved: ``is_reserved`` is False (planning sets it when it picks
      a slot); and
    * not the destination of another open order: no order that is not
      complete, failed or cancelled has it as ``to_position``, and no leg of
      such an order ends on it. This catches a slot an order points at even
      if its ``is_reserved`` flag was never set or was cleared.

    Capacity counts every such slot. Stacking order is enforced by
    ``choose_slots`` (Yael, 5 Nov 2026): fill ground (tier 1) first, then
    tier 2, then tier 3, and so on. A tier-N slot is only chosen when every
    lower tier of the same pile is already held (occupied, reserved, an
    open-order target, or chosen earlier in the same draw), so planning
    never places a floating container. "Held" is checked against the
    database (``held_slot_keys``), not inferred from absence in ``free``: a
    lower tier that is missing (no Position row) or empty but not plannable
    (no operational position) does not hold anything up (BE review N-3 on
    #38, 15 Nov), so the slot above it is not eligible.
    ``discharge_no_free_slot`` is raised only when every tier is full. If stacking rules leave fewer slots
    reachable than ``free`` holds (a gappy grid), ``choose_slots`` raises
    ``SlotShortageError`` (a ``ValueError``) and discharge planning maps it to
    ``discharge_no_free_slot`` too, never a bare ``ValueError``.

    Bay/length (TL + BE, 15 Nov): with the containers passed in, each slot
    must also fit its container by ``Pile.can_accept_container`` (the rule the
    RTG drop enforces: 20' in odd bays, 40'/45' in even bays). No compatible
    free slot is ``discharge_no_free_slot`` as well.

Releasing a reservation (D-047, ``release_reserved_slot``)
    Yael (5 Nov 2026): cancelling an order, or completing it by exception,
    frees its reserved yard slot immediately, in the same transaction, if the
    container has not yet been dropped there. An already-dropped container
    keeps the slot as occupied. The helper is idempotent (a second cancel is
    a no-op) and locks the ``Position`` with ``select_for_update``, the same
    way discharge planning locks the free list.

How seeding works (``slot_rng``)
    The draw uses its own ``random.Random`` (never the global ``random``
    state), seeded with the string ``TCL-012:<scenario seed>:<visit id>``.
    The scenario seed is the ``SEED`` of the last ``make scenario-reset``
    (stored in ``ScenarioSeed`` by ``reset_scenario``), else
    ``DEFAULT_PLANNING_SEED`` (1, the K7 default). Ids restart on a reset, so
    the same seed gives the same slots after a reset, in any process. The QA M2
    reset epoch is deliberately not part of the seed: it is fresh on every
    reset, so it would make two resets differ.
"""

import logging
import random

from django.db.models import Exists, OuterRef, Q

from minitos_core.models import Order, OrderLeg, OrderRoutePlan, Position, ScenarioSeed

logger = logging.getLogger(__name__)

IMPORT_BLOCK_NAME = 'BLOCK01'
DEFAULT_PLANNING_SEED = 1
RNG_PREFIX = 'TCL-012'
# Orders in these states no longer hold their destination slot.
CLOSED_ORDER_STATUSES = (Order.COMPLETE, Order.FAILED, Order.CANCELLED)


class SlotShortageError(ValueError):
    """``choose_slots`` cannot return ``needed`` slots; ``available`` is how many
    it could (all of ``free``, or fewer when stacking makes some unreachable)."""

    def __init__(self, message, *, needed, available, size_mismatch=False):
        super().__init__(message)
        self.needed = needed
        self.available = available
        self.size_mismatch = size_mismatch


def scenario_seed():
    """The seed of the current scenario run, or DEFAULT_PLANNING_SEED."""
    row = ScenarioSeed.objects.order_by('-pk').first()
    return row.seed if row is not None else DEFAULT_PLANNING_SEED


def set_scenario_seed(scenario, seed):
    """Replace the stored scenario seed (called by ``reset_scenario``).

    ``ScenarioSeed`` is a singleton (unique ``key``), so this is an upsert on
    that key, never a second insert.
    """
    row, _ = ScenarioSeed.objects.update_or_create(
        key=ScenarioSeed.SINGLETON_KEY,
        defaults={'scenario': scenario, 'seed': int(seed)},
    )
    return row


def slot_rng(visit, seed=None):
    """Yard-slot RNG for ``visit``: same scenario seed + visit id -> same draw."""
    seed = scenario_seed() if seed is None else int(seed)
    return random.Random(f'{RNG_PREFIX}:{seed}:{visit.pk}')


def open_order_destination_q():
    """Q on Position: some open order or leg already ends on this slot."""
    open_orders = Order.objects.exclude(status__in=CLOSED_ORDER_STATUSES)
    order_target = open_orders.filter(to_position__stack_position=OuterRef('pk'))
    leg_target = OrderLeg.objects.filter(
        end_position__stack_position=OuterRef('pk'),
    ).exclude(order__status__in=CLOSED_ORDER_STATUSES)
    return Q(Exists(order_target)) | Q(Exists(leg_target))


def free_yard_slots(grid):
    """Unheld slots of ``grid`` up to ``grid.max_tier`` (see the module docstring).

    Returns every unheld slot with an operational position; ``choose_slots``
    then fills the lowest available tier first and refuses floating stacks.
    Ordered by pk so a locked ``select_for_update`` list stays stable.
    """
    return (
        Position.objects.filter(
            pile__storage_grid=grid,
            tier__lte=grid.max_tier,
            container_visit__isnull=True,
            is_reserved=False,
            is_occupied=False,
            operational_position__isnull=False,
        )
        .exclude(open_order_destination_q())
        .select_related('operational_position', 'pile')
        .order_by('pk')
    )


def held_slot_q():
    """Q on Position: something is (or will be) in this slot, so it holds up
    the tier above: a container, the occupied flag, a reservation, or an open
    order/leg ending on it."""
    return (
        Q(container_visit__isnull=False)
        | Q(is_occupied=True)
        | Q(is_reserved=True)
        | open_order_destination_q()
    )


def held_slot_keys(pile_ids):
    """``{(pile_id, tier)}`` of the held slots on ``pile_ids`` (``held_slot_q``)."""
    return set(
        Position.objects.filter(pile_id__in=set(pile_ids))
        .filter(held_slot_q())
        .values_list('pile_id', 'tier')
    )


def choose_slots(free, count, rng, containers=None, held_keys=None):
    """``count`` distinct slots from ``free``, lowest tier first, no floating.

    Within each tier, picks randomly with ``rng``. Tier 1 is exhausted before
    any tier 2 is considered, and so on up to the highest tier present in
    ``free``. A tier-N slot is only eligible when every lower tier of the same
    pile is already held (in ``held_keys``), or was chosen earlier in this
    call (so a single plan can fill ground and then stack on the piles it
    just reserved). A lower tier that is neither, e.g. a missing Position or
    an empty slot with no operational position, makes the slot above it
    ineligible: no floating stack on a gappy grid (BE review N-3 on #38).

    ``held_keys`` (optional): ``{(pile_id, tier)}`` of held slots. When
    omitted and ``free`` has a slot above tier 1, it is read from the database
    with ``held_slot_keys`` for the piles in ``free``.

    ``containers`` (optional, one container visit per slot, in order): slot
    ``i`` of the result must fit ``containers[i]`` by the same bay/length rule
    the drop enforces (``Pile.can_accept_container``: 20' in odd bays, 40' and
    45' in even bays), so planning never reserves a slot the RTG drop would
    refuse. Containers are drawn in groups of the same ISO length, in order of
    first appearance, sharing the stacking state.

    Raises ``SlotShortageError`` (a ``ValueError``) if ``free`` is too small,
    or stacking rules or bay/length compatibility leave fewer than ``count``
    slots to choose from.
    """
    free = list(free)
    if count > len(free):
        raise SlotShortageError(
            f'{count} slot(s) needed, {len(free)} free.', needed=count, available=len(free),
        )
    if count == 0:
        return []
    if containers is not None and len(containers) != count:
        raise ValueError(f'{len(containers)} container(s) given for {count} slot(s).')

    if held_keys is None:
        stacked_piles = {slot.pile_id for slot in free if slot.tier > 1}
        held_keys = held_slot_keys(stacked_piles) if stacked_piles else set()
    chosen_keys = set()
    taken = set()

    def draw(pool, need):
        """The tier walk over ``pool`` (shares ``chosen_keys``/``taken``)."""
        picked = []
        remaining = {slot.pk: slot for slot in pool if slot.pk not in taken}
        if not remaining:
            return picked
        max_tier = max(slot.tier for slot in remaining.values())
        for tier in range(1, max_tier + 1):
            left = need - len(picked)
            if left <= 0:
                break
            candidates = []
            for slot in remaining.values():
                if slot.tier != tier:
                    continue
                if all(
                    (slot.pile_id, lower) in chosen_keys
                    or (slot.pile_id, lower) in held_keys
                    for lower in range(1, tier)
                ):
                    candidates.append(slot)
            if not candidates:
                continue
            for slot in rng.sample(candidates, min(left, len(candidates))):
                picked.append(slot)
                chosen_keys.add((slot.pile_id, slot.tier))
                taken.add(slot.pk)
                del remaining[slot.pk]
        return picked

    if containers is None:
        chosen = draw(free, count)
        if len(chosen) < count:
            raise SlotShortageError(
                f'{count} slot(s) needed, only {len(chosen)} reachable without a floating stack.',
                needed=count, available=len(chosen),
            )
        return chosen

    groups = {}
    for index, container_visit in enumerate(containers):
        groups.setdefault(container_length(container_visit), []).append(index)
    result = [None] * count
    for indexes in groups.values():
        sample = containers[indexes[0]]
        pool = [slot for slot in free if slot.pile.can_accept_container(sample)[0]]
        picked = draw(pool, len(indexes))
        if len(picked) < len(indexes):
            available = count - len(indexes) + len(picked)
            if len(pool) < len(indexes):
                raise SlotShortageError(
                    f"{len(indexes)} {container_length(sample)}' container(s) need a slot, only "
                    f'{len(pool)} free slot(s) fit that length (bay rule).',
                    needed=count, available=available, size_mismatch=True,
                )
            raise SlotShortageError(
                f'{count} slot(s) needed, only {available} reachable without a floating stack.',
                needed=count, available=available,
            )
        for index, slot in zip(indexes, picked):
            result[index] = slot
    return result


def container_length(container_visit):
    """ISO length code of a container visit ('20', '40', '45'), or None."""
    unit = getattr(container_visit, 'container_number', None)
    iso_type = getattr(unit, 'iso_type', None)
    return getattr(iso_type, 'length', None)


def prefer_import_block(lanes):
    """Order block lanes so the one serving BLOCK01 comes first; if there is
    one, only it is returned (no fallback to another block when it is full).

    So if BLOCK01 has a transfer interface but no usable lane position,
    planning fails with ``discharge_no_block_lane`` instead of spilling into
    another block's lane (by design: no silent spill)."""
    lanes = list(lanes)
    import_lanes = [lane for lane in lanes if lane.storage_grid.name == IMPORT_BLOCK_NAME]
    return import_lanes or lanes


def reserved_slot_for_order(order):
    """The inventory slot this order reserved, or None.

    Prefers ``order.to_position.stack_position`` (discharge planning writes
    the yard slot there). Falls back to the last active-plan leg that ends
    on a stack position, so a slot only held via the leg is still released.
    """
    to_position = order.to_position
    if to_position is not None and to_position.stack_position_id:
        return to_position.stack_position
    leg = (
        order.order_legs.filter(
            route_plan__status=OrderRoutePlan.ACTIVE,
            end_position__stack_position__isnull=False,
        )
        .select_related('end_position__stack_position')
        .order_by('-sequence')
        .first()
    )
    if leg is not None:
        return leg.end_position.stack_position
    return None


def release_reserved_slot(order):
    """Clear ``is_reserved`` on the order's planned yard slot, if not dropped.

    Idempotent. Locks the ``Position`` with ``select_for_update(of=('self',))``
    so a concurrent planning run cannot draw the slot until this transaction
    commits (or rolls back). No-op when the order has no yard destination,
    the slot is already unreserved, or the slot already holds a container
    (the drop has happened; the slot stays occupied). Logs at INFO with the
    order id and the slot code only when the flag actually changes.
    """
    slot = reserved_slot_for_order(order)
    if slot is None:
        return None
    locked = (
        Position.objects.select_for_update(of=('self',))
        .select_related('operational_position', 'pile', 'pile__storage_grid')
        .filter(pk=slot.pk)
        .first()
    )
    if locked is None:
        return None
    if locked.container_visit_id is not None or locked.is_occupied:
        return locked
    if not locked.is_reserved:
        return locked
    locked.is_reserved = False
    locked.save(update_fields=['is_reserved'])
    op = locked.operational_position if hasattr(locked, 'operational_position') else None
    code = op.code if op is not None else str(locked)
    logger.info(
        'D-047 released reserved yard slot %s for order %s',
        code, order.pk,
    )
    return locked
