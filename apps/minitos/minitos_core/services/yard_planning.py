"""TCL-012 (#10): random free yard slot per discharge order, seeded (D-016).

Called by TCL-010 discharge planning (``services.discharge_planning``) when a
vessel visit goes InOperation. This module owns three things:

Which block
    The MVP import block is ``BLOCK01`` (``IMPORT_BLOCK_NAME``, the same block
    as the D-013 exception block). If the current layout has a truck transfer
    lane serving a grid named BLOCK01, that block is used, even when it is
    full; a full BLOCK01 is an error, never a silent spill into another block.
    Only a layout with no BLOCK01 lane at all keeps TCL-010's rule (first
    block lane by code).

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
    never places a floating container. ``discharge_no_free_slot`` is raised
    only when every tier is full.

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

import random

from django.db.models import Exists, OuterRef, Q

from minitos_core.models import Order, OrderLeg, Position, ScenarioSeed

IMPORT_BLOCK_NAME = 'BLOCK01'
DEFAULT_PLANNING_SEED = 1
RNG_PREFIX = 'TCL-012'
# Orders in these states no longer hold their destination slot.
CLOSED_ORDER_STATUSES = (Order.COMPLETE, Order.FAILED, Order.CANCELLED)


def scenario_seed():
    """The seed of the current scenario run, or DEFAULT_PLANNING_SEED."""
    row = ScenarioSeed.objects.order_by('-pk').first()
    return row.seed if row is not None else DEFAULT_PLANNING_SEED


def set_scenario_seed(scenario, seed):
    """Replace the stored scenario seed (called by ``reset_scenario``)."""
    ScenarioSeed.objects.all().delete()
    return ScenarioSeed.objects.create(scenario=scenario, seed=int(seed))


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


def choose_slots(free, count, rng):
    """``count`` distinct slots from ``free``, lowest tier first, no floating.

    Within each tier, picks randomly with ``rng``. Tier 1 is exhausted before
    any tier 2 is considered, and so on up to the highest tier present in
    ``free``. A tier-N slot is only eligible when every lower tier of the same
    pile is already held outside ``free``, or was chosen earlier in this call
    (so a single plan can fill ground and then stack on the piles it just
    reserved). The caller checks there are enough unheld slots overall; this
    raises if stacking rules make fewer than ``count`` reachable.
    """
    free = list(free)
    if count > len(free):
        raise ValueError(f'{count} slot(s) needed, {len(free)} free.')
    if count == 0:
        return []

    free_keys = {(slot.pile_id, slot.tier) for slot in free}
    remaining = {slot.pk: slot for slot in free}
    chosen = []
    chosen_keys = set()
    max_tier = max(slot.tier for slot in free)

    for tier in range(1, max_tier + 1):
        need = count - len(chosen)
        if need <= 0:
            break
        candidates = []
        for slot in remaining.values():
            if slot.tier != tier:
                continue
            if all(
                (slot.pile_id, lower) in chosen_keys
                or (slot.pile_id, lower) not in free_keys
                for lower in range(1, tier)
            ):
                candidates.append(slot)
        if not candidates:
            continue
        pick = rng.sample(candidates, min(need, len(candidates)))
        for slot in pick:
            chosen.append(slot)
            chosen_keys.add((slot.pile_id, slot.tier))
            del remaining[slot.pk]

    if len(chosen) < count:
        raise ValueError(
            f'{count} slot(s) needed, only {len(chosen)} reachable without a floating stack.'
        )
    return chosen


def prefer_import_block(lanes):
    """Order block lanes so the one serving BLOCK01 comes first; if there is
    one, only it is returned (no fallback to another block when it is full)."""
    lanes = list(lanes)
    import_lanes = [lane for lane in lanes if lane.storage_grid.name == IMPORT_BLOCK_NAME]
    return import_lanes or lanes
