"""TCL-006 scenario support for terminal-oes: reset, readiness and canonical dump.

OES owns no seed data: after ``scenario_reset`` its projections are rebuilt
from the MiniTOS ``site.created`` / ``configuration.published`` events, exactly
as in normal operation. The reset only has to forget the previous run,
including ``SiteProjection.source_revision``, otherwise a re-seeded MiniTOS
(revision 1 again) would be ignored as stale.
"""

import hashlib
import json
import re
import time
import uuid
from datetime import date, datetime

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management.color import no_style
from django.db import connection, models, transaction

from .models import ExecutionOrder, SimulationSession, SiteProjection

APP_LABEL = "execution"
SCENARIOS = ("import_v1",)

VOLATILE_JSON_KEYS = frozenset({
    "event_id", "occurred_at", "generated_at", "published_at", "snapshot_checksum", "checksum",
})
VOLATILE_FIELDS = {
    # Source event ids are uuid4 in MiniTOS (BE-owned) and the snapshot
    # carries generated_at, so its checksum differs per publish (D-016).
    "inboxevent": {"event_id"},
    # Delivery bookkeeping depends on when the outbox publisher ran.
    "outboxevent": {"attempts", "last_error", "published_at"},
    "siteprojection": {"snapshot_checksum"},
    "simulationsession": {"configuration_checksum"},
}
_ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def app_models():
    config = apps.get_app_config(APP_LABEL)
    return [
        model for model in config.get_models(include_auto_created=True)
        if model._meta.managed and not model._meta.proxy
    ]


def active_sessions():
    return SimulationSession.objects.filter(actual_state__in=SimulationSession.ACTIVE_STATES)


@transaction.atomic
def reset_execution_tables(*, force=False):
    """Empty every ``execution`` table (auth users survive) and restart sequences."""
    running = list(active_sessions().values_list("pk", flat=True))
    if running and not force:
        raise ValidationError(
            f"{len(running)} simulation session(s) are still active; stop the simulation "
            "first (OES Simulations page) or pass --force."
        )
    tables = sorted({model._meta.db_table for model in app_models()})
    statements = connection.ops.sql_flush(no_style(), tables, reset_sequences=True, allow_cascade=False)
    if connection.vendor == "postgresql":
        # TRUNCATE fails while deferred FK checks are pending in this transaction.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        connection.ops.execute_sql_flush(statements)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    else:
        connection.ops.execute_sql_flush(statements)
    return len(tables)


def wait_for_ready_site(site_code=None, *, min_revision=1, timeout=60.0, interval=1.0,
                        sleep=time.sleep, clock=time.monotonic):
    """Poll until a Site projection is READY at ``min_revision`` or later."""
    deadline = clock() + timeout
    while True:
        sites = SiteProjection.objects.filter(
            projection_status=SiteProjection.READY, source_revision__gte=min_revision,
        )
        if site_code:
            sites = sites.filter(code=site_code)
        site = sites.order_by("code").first()
        if site is not None:
            return site
        if clock() >= deadline:
            raise TimeoutError(
                f"No READY Site projection{f' {site_code!r}' if site_code else ''} at revision "
                f">= {min_revision} after {timeout:.0f}s. Are the MiniTOS outbox publisher, "
                "the broker and the OES consumer running?"
            )
        sleep(interval)


def next_dispatchable_order(tos_order_id=None):
    orders = ExecutionOrder.objects.filter(
        status__in=[ExecutionOrder.RELEASED, ExecutionOrder.IN_PROGRESS],
    )
    if tos_order_id is not None:
        orders = orders.filter(tos_order_id=tos_order_id)
    order = orders.order_by("priority", "tos_order_id", "pk").first()
    if order is None:
        raise ValidationError(
            "No released order to dispatch; run `make scenario-step STEP=release-discharge` first."
        )
    return order


def _canonical_value(value):
    if isinstance(value, datetime):
        return "<time>"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): ("<volatile>" if key in VOLATILE_JSON_KEYS else _canonical_value(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, str) and _ISO_TIMESTAMP.match(value):
        return "<time>"
    return value


def canonical_dump():
    result = {}
    for model in sorted(app_models(), key=lambda item: item._meta.db_table):
        volatile = VOLATILE_FIELDS.get(model._meta.model_name, set())
        rows = []
        for instance in model._default_manager.order_by("pk"):
            row = {}
            for field in model._meta.concrete_fields:
                if field.attname in volatile:
                    row[field.attname] = "<volatile>"
                    continue
                value = getattr(instance, field.attname)
                if isinstance(field, models.DateTimeField) and value is not None:
                    value = "<time>"
                row[field.attname] = _canonical_value(value)
            rows.append(row)
        rows.sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
        result[model._meta.db_table] = rows
    return result


def canonical_checksum(dump):
    encoded = json.dumps(dump, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
