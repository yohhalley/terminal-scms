"""QA M2: reset epoch (run generation marker) for scenario-reset isolation.

After ``make scenario-reset`` the ids restart at 1 and the Site uuid5 is the
same, so a message from the previous run (a queued QoS 1 delivery, or an
unpublished outbox row) is otherwise valid against the new run. The reset
command stores one opaque epoch string; producers stamp it into every envelope
as the top-level ``reset_epoch`` key and consumers ignore envelopes whose epoch
is not the current one.

Rules (both services, see docs/SCENARIO_RESET.md):
- No epoch stored (normal operation, tests, a reset without ``--reset-epoch``):
  nothing is stamped and every envelope is accepted, exactly as before.
- Epoch stored: outgoing envelopes carry it; an incoming envelope is accepted
  only if its ``reset_epoch`` equals it. A different or missing value is
  ignored (logged, no inbox row, no state change).
"""

import logging
import re
import threading
from collections import Counter

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

ENVELOPE_KEY = 'reset_epoch'
EPOCH_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$')


def validate_epoch(value):
    value = str(value or '').strip()
    if not EPOCH_PATTERN.match(value):
        raise ValidationError(
            'A reset epoch is 1-64 characters: letters, digits, ".", "_", ":" or "-".'
        )
    return value


def current_epoch():
    from minitos_core.models import ScenarioResetEpoch

    row = ScenarioResetEpoch.objects.order_by('-pk').first()
    return row.epoch if row else ''


def set_epoch(value, *, scenario=''):
    """Replace the stored epoch (``None``/empty clears it: no fence)."""
    from minitos_core.models import ScenarioResetEpoch

    ScenarioResetEpoch.objects.all().delete()
    if not value:
        return ''
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
    """Return why an incoming envelope belongs to another run, or ``''``."""
    epoch = current_epoch()
    if not epoch:
        return ''
    incoming = payload.get(ENVELOPE_KEY)
    if incoming == epoch:
        return ''
    if incoming in (None, ''):
        return f'no reset_epoch, current epoch is {epoch}'
    return f'reset_epoch {incoming!r} is not the current epoch {epoch}'


# Ignored messages per message type (schema) since the process started. The
# consumers are long-running processes, so the running total in every log line
# shows how many drops a run has had; tests read it through ignored_count().
_ignored = Counter()
_ignored_lock = threading.Lock()


def ignored_count(schema=None):
    """Messages ignored as stale by this process (for one schema, or all)."""
    with _ignored_lock:
        return _ignored[schema] if schema is not None else sum(_ignored.values())


def reset_ignored_count():
    with _ignored_lock:
        _ignored.clear()


def ignore_if_stale(topic, payload):
    """Log (INFO), count and return True when ``payload`` must be ignored (M2).

    Strict policy (Tech Lead, 2026-11-02): with an epoch stored, a different or
    missing ``reset_epoch`` is ignored.
    """
    if not stale_reason(payload):
        return False
    schema = payload.get('schema') or 'unknown'
    with _ignored_lock:
        _ignored[schema] += 1
        total = sum(_ignored.values())
    logger.info(
        'Ignored stale message type=%s event_id=%s topic=%s: reset_epoch seen=%s stored=%s '
        '(previous scenario run; ignored_total=%d).',
        schema, payload.get('event_id'), topic,
        payload.get(ENVELOPE_KEY) or 'none', current_epoch(), total,
    )
    return True
