"""Shared helpers for TCL-006 scenarios: reset, deterministic ids, canonical dump."""

import hashlib
import json
import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from django.apps import apps
from django.core.management.color import no_style
from django.db import connection, models, transaction

# Fixed namespace for scenario identities. Changing it changes every seeded
# UUID (Enterprise, Site), so it is part of the TCL-006 determinism contract.
SCENARIO_NAMESPACE = uuid.UUID('7c0e5b1a-6d1e-4c55-9a2b-0000000000c6')

APP_LABEL = 'minitos_core'

# Values that legitimately differ between two resets (D-016: "same states in the
# same order; identical messages are not required"). The canonical dump blanks
# them; everything else must match byte for byte.
VOLATILE_JSON_KEYS = frozenset({
    'event_id', 'occurred_at', 'generated_at', 'published_at',
    'snapshot_checksum',
})
VOLATILE_FIELDS = {
    # Outbox event ids are uuid4 in the existing site-sync path (BE-owned);
    # delivery bookkeeping depends on when the outbox publisher ran.
    'integrationoutbox': {'event_id', 'attempts', 'last_error', 'published_at'},
    # The snapshot payload carries generated_at, so its checksum varies.
    'siteconfigurationsnapshot': {'checksum'},
}


# ISO 8601 timestamps serialized inside JSON payloads (snapshot rows, events).
_ISO_TIMESTAMP = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}')


def scenario_uuid(*parts):
    """Deterministic UUID5 for a seeded identity, e.g. ``scenario_uuid('site', code)``."""
    return uuid.uuid5(SCENARIO_NAMESPACE, ':'.join(str(part) for part in parts))


def iso6346_check_digit(owner_and_serial):
    """ISO 6346 check digit for the first 10 characters (e.g. ``MSCU123456``)."""
    value = owner_and_serial.upper()
    if len(value) != 10:
        raise ValueError('ISO 6346 needs 4 letters and 6 digits.')
    letters = {}
    number = 10
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        if number % 11 == 0:
            number += 1
        letters[letter] = number
        number += 1
    total = 0
    for index, char in enumerate(value):
        digit = letters[char] if char.isalpha() else int(char)
        total += digit * (2 ** index)
    return (total % 11) % 10


def container_number(owner_code, serial):
    base = f'{owner_code.upper()}{int(serial):06d}'
    return f'{base}{iso6346_check_digit(base)}'


def app_models():
    """Every concrete table of the MiniTOS app, M2M through tables included."""
    config = apps.get_app_config(APP_LABEL)
    return [
        model for model in config.get_models(include_auto_created=True)
        if model._meta.managed and not model._meta.proxy
    ]


@transaction.atomic
def wipe_minitos_tables():
    """Empty every ``minitos_core`` table and restart its id sequences.

    Only this app's tables are touched: Django auth users, sessions and admin
    logs survive a reset, so ``make create-minitos-admin`` is needed only once.
    """
    tables = sorted({model._meta.db_table for model in app_models()})
    statements = connection.ops.sql_flush(
        no_style(), tables, reset_sequences=True, allow_cascade=False,
    )
    _execute_flush(statements)
    return len(tables)


def _execute_flush(statements):
    if connection.vendor != 'postgresql':
        connection.ops.execute_sql_flush(statements)
        return
    # PostgreSQL refuses TRUNCATE while deferred FK checks are pending in the
    # same transaction (e.g. a second reset inside one test transaction).
    # Running them now, then restoring Django's deferred default, avoids that.
    # ALL DEFERRED is only that default while no constraint is Deferrable.IMMEDIATE.
    with connection.cursor() as cursor:
        cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    connection.ops.execute_sql_flush(statements)
    with connection.cursor() as cursor:
        cursor.execute('SET CONSTRAINTS ALL DEFERRED')


def _canonical_value(value):
    if isinstance(value, datetime):
        return '<time>'
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): ('<volatile>' if key in VOLATILE_JSON_KEYS else _canonical_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, str) and _ISO_TIMESTAMP.match(value):
        return '<time>'
    return value


def canonical_dump(model_list=None):
    """Return ``{table: [rows]}`` with volatile values blanked, rows in a stable order."""
    result = {}
    for model in sorted(model_list or app_models(), key=lambda item: item._meta.db_table):
        volatile = VOLATILE_FIELDS.get(model._meta.model_name, set())
        rows = []
        for instance in model._default_manager.order_by('pk'):
            row = {}
            for field in model._meta.concrete_fields:
                if field.attname in volatile:
                    row[field.attname] = '<volatile>'
                    continue
                value = getattr(instance, field.attname)
                if isinstance(field, models.DateTimeField) and value is not None:
                    # Wall-clock stamps (created_at, published_at, started_at...)
                    # differ per run; only their presence is compared. Seeded
                    # business times are asserted separately in the tests.
                    value = '<time>'
                elif isinstance(value, (date, time)):
                    value = value.isoformat()
                row[field.attname] = _canonical_value(value)
            rows.append(row)
        rows.sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
        result[model._meta.db_table] = rows
    return result


def canonical_checksum(dump):
    encoded = json.dumps(dump, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
