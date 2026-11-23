"""Display rules for the demo screens (D-041, D-031, D-032; decided by D-077).

Used by the discharge list page (S2), the vessel visit panel and the Django
admin changelists (S3). Yael answered the board-7 demo-screens card on
22 Nov 2026 (D-077); each rule is still one constant or function here.

- CONTAINER_NUMBER_FORMAT: 'plain', one string (``MSCU1234566``). D-077 Q5.
- VESSEL_SLOT_FORMAT: 'compact' bay-row-tier, shown as 6 characters BBRRTT
  (``330682`` = Bay 33 · Row 06 · Tier 82) with a 2-digit bay; a bay above 99
  keeps 3 digits (D-077 Q5, TL 23 Nov). Display only: the stored slot code and
  ``logical_address.stow`` stay the 7-digit ISO 9711 BBBRRTT (``0330682``).
- TIME_DISPLAY_ZONE: 'terminal', the terminal's ``Location.timezone``, and
  every displayed time is labelled with the zone (D-077 Q4: "show terminal
  local time (ensure this is clear at all times)").
- MISSING_VALUE: ``-`` for missing info (D-077: 'default "-" for missing info').
- TIME_FORMAT: the ``strftime`` format of every time on S2, the panel and the
  admin. Templates use ``{{ value|demo_time:location }}`` and the zone label.
- stowage_sort_key: discharge list rows in stowage order: per bay, deck before
  hold, top tier first (D-077), and within a tier the row nearest the quay
  first (D-079). "Nearest the quay" is ``row_quay_distance`` with the
  berthing side ``QUAY_SIDE``.
- check_digit_warning: a wrong ISO 6346 check digit only warns, never
  rejects (D-077).
- format_weight: weight in tonnes; seeded defaults are in
  ``scenarios/import_v1.py`` (D-077 Q6).

One received time per vessel call (``CarrierVisit.discharge_list_received_at``,
D-077 Q3) and the seeded 48 h lead (``import_v1.DISCHARGE_LIST_LEAD_TIME``,
D-077 Q2) are not display choices.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONTAINER_NUMBER_FORMAT = 'plain'
VESSEL_SLOT_FORMAT = 'compact'
TIME_DISPLAY_ZONE = 'terminal'
MISSING_VALUE = '-'
TIME_FORMAT = '%Y-%m-%d %H:%M'


def format_container_number(number):
    if not number:
        return MISSING_VALUE
    number = str(number)
    if CONTAINER_NUMBER_FORMAT == 'spaced' and len(number) == 11:
        return f'{number[:4]} {number[4:10]} {number[10]}'
    return number


def format_vessel_slot(stow):
    """Display a stored 7-digit BBBRRTT stow code.

    'compact': BBRRTT, 6 characters with a 2-digit bay (``0330682`` ->
    ``330682``); a bay above 99 keeps 3 digits (``1010682``). 'spelled':
    ``Bay 33 · Row 06 · Tier 82``. Anything that isn't 7 digits is shown as is.
    """
    if not stow:
        return MISSING_VALUE
    if len(stow) != 7 or not stow.isdigit():
        return stow
    bay = int(stow[:3])
    bay_text = f'{bay:02d}' if bay <= 99 else f'{bay:03d}'
    if VESSEL_SLOT_FORMAT == 'spelled':
        return f'Bay {bay_text} · Row {stow[3:5]} · Tier {stow[5:]}'
    return f'{bay_text}{stow[3:]}'


def display_timezone_name(location):
    if TIME_DISPLAY_ZONE == 'terminal' and location is not None and location.timezone:
        return location.timezone
    return 'UTC'


def format_local_time(value, location, *, with_zone=False):
    """``YYYY-MM-DD HH:MM`` in the display zone (optionally followed by the zone)."""
    if value is None:
        return MISSING_VALUE
    zone_name = display_timezone_name(location)
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        zone_name, zone = 'UTC', ZoneInfo('UTC')
    text = value.astimezone(zone).strftime(TIME_FORMAT)
    return f'{text} {zone_name}' if with_zone else text


# A tier of 80 or more is on deck (ISO 9711: deck tiers from 82, hold from 02).
DECK_TIER_FROM = 80

# D-079: within a tier, discharge the row nearest the quay first. Which rows
# are nearest depends on the vessel side alongside the quay. No model or seed
# field records it yet, so this is a documented default (TL, 23 Nov 2026):
# 'port' = port side alongside; 'starboard' = starboard side alongside.
QUAY_SIDE = 'port'


def row_quay_distance(row, quay_side=None):
    """Rank of a row by distance from the quay (0 = nearest), D-079.

    ISO 9711 row numbering: 00 on the centre line, even rows to port (02, 04,
    ... outwards), odd rows to starboard (01, 03, ... outwards). With the port
    side alongside the outermost even row is nearest the quay and the outermost
    odd row is farthest; with the starboard side alongside it is the reverse.
    The value only orders rows; it is not a distance in metres.
    """
    side = quay_side or QUAY_SIDE
    # Position across the vessel, port negative, starboard positive.
    if row == 0:
        across = 0
    elif row % 2 == 0:
        across = -(row // 2)
    else:
        across = (row + 1) // 2
    return across if side == 'port' else -across


def stowage_sort_key(stow):
    """Sort key for a BBBRRTT stow code: stowage / discharge order.

    Per bay (ascending), deck before hold, top tier first (D-077), then within
    the tier the row nearest the quay first (D-079, ``row_quay_distance``).
    Rows without a real slot come last.
    """
    if not stow or len(stow) != 7 or not stow.isdigit():
        return (1, 0, 0, 0, 0)
    bay, row, tier = int(stow[:3]), int(stow[3:5]), int(stow[5:])
    return (0, bay, 0 if tier >= DECK_TIER_FROM else 1, -tier, row_quay_distance(row))


def check_digit_warning(number):
    """A warning text if ``number``'s ISO 6346 check digit looks wrong, else ``None``.

    D-077: "wrong ISO check digit should not be a problem for now. If anything
    I would add a warning that the digit might be wrong but not reject it."
    Nothing calls this to reject a save. A number that isn't 4 letters plus 7
    digits can't be checked and gets the same warning.
    """
    if not number:
        return None
    from .scenarios.base import iso6346_check_digit

    number = str(number).upper()
    if len(number) != 11 or not number[:4].isalpha() or not number[4:].isdigit():
        return 'ISO 6346 check digit may be wrong (not 4 letters and 7 digits).'
    if str(iso6346_check_digit(number[:10])) != number[10]:
        return f'ISO 6346 check digit may be wrong (expected {iso6346_check_digit(number[:10])}).'
    return None


def format_weight(weight_kg):
    """``18.4 t`` from kilograms; 0 or empty is missing (the field defaults to 0)."""
    if not weight_kg:
        return MISSING_VALUE
    return f'{weight_kg / 1000:.1f} t'
