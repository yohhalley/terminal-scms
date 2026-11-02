"""QA M2: reset epoch (run generation marker) for scenario-reset isolation.

Mirror of ``minitos_core/services/reset_epoch.py`` (each service owns its own
copy and its own table; nothing is shared across databases). After
``make scenario-reset`` both services hold the same epoch:

- No epoch stored (normal operation, tests, a reset without ``--reset-epoch``):
  nothing is stamped and every envelope is accepted, exactly as before.
- Epoch stored: outgoing ``scms.execution.event.v1`` envelopes carry it as the
  top-level ``reset_epoch`` key; an incoming MiniTOS envelope is accepted only
  if its ``reset_epoch`` equals it. A different or missing value is ignored
  (logged, no inbox row, no state change).

Sparkplug traffic from Godot is not fenced here: command and session ids are
uuid4, so results for the previous run's commands are already rejected as
unknown commands or a foreign session.
"""

import logging
import re

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

ENVELOPE_KEY = "reset_epoch"
EPOCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def validate_epoch(value):
    value = str(value or "").strip()
    if not EPOCH_PATTERN.match(value):
        raise ValidationError(
            'A reset epoch is 1-64 characters: letters, digits, ".", "_", ":" or "-".'
        )
    return value


def current_epoch():
    from execution.models import ScenarioResetEpoch

    row = ScenarioResetEpoch.objects.order_by("-pk").first()
    return row.epoch if row else ""


def set_epoch(value, *, scenario=""):
    """Replace the stored epoch (``None``/empty clears it: no fence)."""
    from execution.models import ScenarioResetEpoch

    ScenarioResetEpoch.objects.all().delete()
    if not value:
        return ""
    epoch = validate_epoch(value)
    ScenarioResetEpoch.objects.create(epoch=epoch, scenario=scenario)
    return epoch


def stamp(payload):
    """Add ``reset_epoch`` to an outgoing envelope when a fence is active."""
    epoch = current_epoch()
    if epoch:
        payload[ENVELOPE_KEY] = epoch
    return payload


def stale_reason(payload):
    """Return why an incoming envelope belongs to another run, or ``""``."""
    epoch = current_epoch()
    if not epoch:
        return ""
    incoming = payload.get(ENVELOPE_KEY)
    if incoming == epoch:
        return ""
    if incoming in (None, ""):
        return f"no reset_epoch, current epoch is {epoch}"
    return f"reset_epoch {incoming!r} is not the current epoch {epoch}"


def ignore_if_stale(topic, payload):
    """Log and return True when ``payload`` must be ignored (M2)."""
    reason = stale_reason(payload)
    if reason:
        logger.warning(
            "Ignored %s %s on %s from a previous scenario run: %s.",
            payload.get("schema"), payload.get("event_id"), topic, reason,
        )
        return True
    return False
