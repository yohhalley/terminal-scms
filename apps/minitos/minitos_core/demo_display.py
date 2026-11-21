"""Display placeholders for the demo screens (D-041, D-031; TCL-demo-screens).

Each choice still waiting on Yael (board 7, card "Demo screens: container
number, vessel slot, 'discharge list received' time") is one constant here, so
an answer is a one-line change. Used by the discharge list page, the vessel
visit panel and the Django admin changelists.

- CONTAINER_NUMBER_FORMAT (card Q2): 'plain' -> ``MSCU1234566``;
  'spaced' -> ``MSCU 123456 6`` (check digit set apart).
- VESSEL_SLOT_FORMAT (card Q7): 'compact' -> ``0330682`` (BBBRRTT);
  'spelled' -> ``Bay 033 · Row 06 · Tier 82``.
- TIME_DISPLAY_ZONE (card Q5): 'terminal' -> the terminal's
  ``Location.timezone`` (the Test Terminal is America/Mexico_City), the same
  as the vessel visit panel; 'UTC' -> UTC. Screens always name the zone.
- MISSING_VALUE (card Q6): what a missing container number, vessel slot or
  time shows (every time cell on S2, the vessel visit panel and the admin).
- TIME_FORMAT: the ``strftime`` format of every time on S2, the vessel visit
  panel (ETA/ETD/ATA/ATD and the received time) and the admin columns. Templates
  use it through ``{{ value|demo_time:location }}`` (templatetags/demo_display_tags.py).

The compact slot ``0330682`` reads Bay 033 · Row 06 · Tier 82 (3-digit bay,
2-digit row, 2-digit tier), never "Bay 03 / Row 30".

The other two placeholders are not display choices: one received time per
vessel call is the model choice (``CarrierVisit.discharge_list_received_at``,
card Q4), and the seeded 48 h lead before berthing is
``scenarios/import_v1.py::DISCHARGE_LIST_LEAD_TIME`` (card Q3 / spec Q27).
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CONTAINER_NUMBER_FORMAT = 'plain'
VESSEL_SLOT_FORMAT = 'compact'
TIME_DISPLAY_ZONE = 'terminal'
MISSING_VALUE = '—'
TIME_FORMAT = '%Y-%m-%d %H:%M'


def format_container_number(number):
    if not number:
        return MISSING_VALUE
    number = str(number)
    if CONTAINER_NUMBER_FORMAT == 'spaced' and len(number) == 11:
        return f'{number[:4]} {number[4:10]} {number[10]}'
    return number


def format_vessel_slot(stow):
    if not stow:
        return MISSING_VALUE
    if VESSEL_SLOT_FORMAT == 'spelled' and len(stow) == 7:
        return f'Bay {stow[:3]} · Row {stow[3:5]} · Tier {stow[5:]}'
    return stow


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
