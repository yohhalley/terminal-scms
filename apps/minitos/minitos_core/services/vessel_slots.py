"""Read-only vessel-slot lookup for the demo screens (D-041, TCL-demo-screens).

A container visit's vessel slot is the ``OperationalPosition`` with
``position_kind='vessel_slot'`` whose ``logical_address.container_visit_id`` is
the visit's pk, in the terminal's **current** layout version, taking the first
match by code: exactly the lookup TCL-010 discharge planning uses
(``discharge_planning._layout`` / ``_vessel_slot``, seeded by import_v1 since
TCL-010b, #46). So S2 and the admin show the same slot that planning uses as
the discharge order's ``from_position`` (QA review of #54, S4). The terminal is
the inbound carrier visit's location (planning's ``visit.location``), or the
container visit's own location when it has no inbound carrier.

The displayed value is ``logical_address.stow``, the ISO 9711 BBBRRTT code:
``0330682`` is Bay 033 · Row 06 · Tier 82. Planning's fallback placeholder
(``<visit>.CV<id>``, ``stow: None``) is not a real slot, so it counts as "no
vessel slot" here. Nothing in this module writes to the database.
"""

import re

from ..models import ContainerVisit, LayoutVersion, OperationalPosition

STOW_RE = re.compile(r'^\d{7}$')


def _stow(position):
    stow = (position.logical_address or {}).get('stow')
    if isinstance(stow, str) and STOW_RE.match(stow):
        return stow
    return None


def _terminal_location_id(container_visit):
    if container_visit.ib_carrier_id is not None:
        return container_visit.ib_carrier.location_id
    return container_visit.location_id


def current_layout_ids(location_ids):
    """``{location_id: current LayoutVersion id}``, the same pick as planning's ``.first()``."""
    result = {}
    for layout_id, location_id in LayoutVersion.objects.filter(
        location_id__in=set(location_ids), is_current=True,
    ).values_list('pk', 'location_id'):
        result.setdefault(location_id, layout_id)
    return result


def vessel_slots_for(container_visits):
    """Map each container visit's pk to its BBBRRTT stow code, or ``None``.

    Takes ``ContainerVisit`` objects (select ``ib_carrier`` to avoid a query
    per row). Two queries for the whole list: the current layouts, then the
    vessel slots.
    """
    container_visits = [visit for visit in container_visits if visit is not None and visit.pk is not None]
    result = {visit.pk: None for visit in container_visits}
    if not container_visits:
        return result
    layouts = current_layout_ids(_terminal_location_id(visit) for visit in container_visits)
    wanted = {}
    for visit in container_visits:
        layout_id = layouts.get(_terminal_location_id(visit))
        if layout_id is not None:
            wanted[(layout_id, visit.pk)] = visit.pk
    if not wanted:
        return result
    positions = OperationalPosition.objects.filter(
        layout_version_id__in={layout_id for layout_id, _ in wanted},
        position_kind=OperationalPosition.VESSEL_SLOT,
        logical_address__container_visit_id__in=sorted({pk for _, pk in wanted}),
    ).order_by('code')
    seen = set()
    for position in positions:
        key = (position.layout_version_id, (position.logical_address or {}).get('container_visit_id'))
        if key in wanted and key not in seen:
            # First match by code, as planning's ``.first()``; a placeholder wins
            # here too if it sorts first, and then shows as missing.
            seen.add(key)
            result[wanted[key]] = _stow(position)
    return result


def vessel_slot_for(container_visit):
    """The BBBRRTT stow code of one container visit (object or pk), or ``None``."""
    if container_visit is None:
        return None
    if not isinstance(container_visit, ContainerVisit):
        container_visit = ContainerVisit.objects.select_related('ib_carrier').filter(pk=container_visit).first()
        if container_visit is None:
            return None
    return vessel_slots_for([container_visit]).get(container_visit.pk)
