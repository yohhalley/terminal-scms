"""Read-only vessel-slot lookup for the demo screens (D-041, TCL-demo-screens).

A container visit's vessel slot is the ``OperationalPosition`` with
``position_kind='vessel_slot'`` whose ``logical_address.container_visit_id`` is
the visit's pk, the same link TCL-010 discharge planning uses
(``discharge_planning._vessel_slot``, seeded by import_v1 since TCL-010b, #46).
The displayed value is ``logical_address.stow``, the ISO 9711 BBBRRTT code.

Planning's fallback placeholder (``<visit>.CV<id>``, ``stow: None``) is not a
real slot, so it counts as "no vessel slot" here. Nothing in this module
writes to the database.
"""

import re

from ..models import OperationalPosition

STOW_RE = re.compile(r'^\d{7}$')


def _stow(position):
    stow = (position.logical_address or {}).get('stow')
    if isinstance(stow, str) and STOW_RE.match(stow):
        return stow
    return None


def vessel_slots_for(container_visit_ids):
    """Map each container visit id to its BBBRRTT stow code, or ``None``.

    One query for the whole list. If several vessel slots point at the same
    visit (e.g. a real one and an old placeholder), the first real one by code
    wins.
    """
    ids = [pk for pk in container_visit_ids if pk is not None]
    result = {pk: None for pk in ids}
    if not ids:
        return result
    positions = OperationalPosition.objects.filter(
        position_kind=OperationalPosition.VESSEL_SLOT,
        logical_address__container_visit_id__in=ids,
    ).order_by('code')
    for position in positions:
        visit_id = (position.logical_address or {}).get('container_visit_id')
        if visit_id in result and result[visit_id] is None:
            result[visit_id] = _stow(position)
    return result


def vessel_slot_for(container_visit):
    """The BBBRRTT stow code of one container visit (or its pk), or ``None``."""
    if container_visit is None:
        return None
    pk = getattr(container_visit, 'pk', container_visit)
    return vessel_slots_for([pk]).get(pk)
