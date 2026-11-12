"""TCL-006 scenario support for terminal-oes: reset, readiness and canonical dump.

OES owns no seed data: after ``scenario_reset`` its projections are rebuilt
from the MiniTOS ``site.created`` / ``configuration.published`` events, exactly
as in normal operation. The reset only has to forget the previous run,
including ``SiteProjection.source_revision``, otherwise a re-seeded MiniTOS
(revision 1 again) would be ignored as stale.
"""

import hashlib
import json
import time
import uuid
from datetime import date, datetime

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management.color import no_style
from django.db import connection, transaction

from .models import ExecutionOrder, SimulationSession, SiteProjection
from .services.reset_epoch import set_epoch, validate_epoch

APP_LABEL = "execution"
SCENARIOS = ("import_v1",)

# Values that legitimately differ between two resets (D-016). QA M1: the
# canonical dump masks exactly this allow-list; every other value is compared
# literally. Only whether a masked value is set (not null) is compared.
# JSON keys are matched by name at any depth of a JSON field.
VOLATILE_JSON_KEYS = frozenset({
    "event_id", "occurred_at", "generated_at", "published_at", "snapshot_checksum", "checksum",
    # QA M2: the reset epoch is a fresh value per make scenario-reset.
    "reset_epoch",
    # QA M1: MiniTOS wall-clock stamps carried in its payloads (snapshot
    # equipment.ches[].state_updated_at, layout.version created_at/updated_at,
    # order.released data.order.released_at). Same list as MiniTOS.
    "state_updated_at", "created_at", "updated_at", "released_at",
})
VOLATILE_FIELDS = {
    # Source event ids are uuid4 in MiniTOS (BE-owned); received/processed
    # are when the consumer ran.
    "inboxevent": {"event_id", "received_at", "processed_at"},
    "enterpriseprojection": {"created_at", "updated_at"},
    # The snapshot carries generated_at, so its checksum differs per publish.
    "siteprojection": {"snapshot_checksum", "created_at", "updated_at", "last_reconciled_at"},
    "simulationsession": {"configuration_checksum", "created_at", "updated_at"},
    # released_at is MiniTOS's wall-clock release moment (payload data.order.released_at).
    "executionorder": {"released_at", "started_at", "created_at", "updated_at"},
    "equipmentstate": {"last_seen_at"},
    "dispatchassignment": {"assigned_at"},
    "equipmentcommand": {"created_at"},
    "outboxevent": {"created_at"},
    "commandresultreceipt": {"received_at"},
    "handoff": {"created_at", "updated_at"},
    # QA M2: one fresh run marker per reset (only its presence is compared).
    "scenarioresetepoch": {"epoch", "created_at"},
}
# Masked even when unset: delivery bookkeeping depends on when the outbox
# publisher ran, so a drained and an undrained queue compare equal.
ALWAYS_MASKED_FIELDS = {
    "outboxevent": {"attempts", "last_error", "published_at"},
}
VOLATILE = "<volatile>"


def app_models():
    config = apps.get_app_config(APP_LABEL)
    return [
        model for model in config.get_models(include_auto_created=True)
        if model._meta.managed and not model._meta.proxy
    ]


def active_sessions():
    return SimulationSession.objects.filter(actual_state__in=SimulationSession.ACTIVE_STATES)


@transaction.atomic
def reset_execution_tables(*, force=False, reset_epoch=None, scenario=""):
    """Empty every ``execution`` table (auth users survive) and restart sequences.

    QA M2: ``reset_epoch`` (the value ``make scenario-reset`` also gives MiniTOS)
    is stored in the same transaction. From then on OES ignores MiniTOS
    envelopes from any other epoch, including the previous run's outbox rows
    that MiniTOS may still publish before its own reset. ``None``: no fence.
    """
    if reset_epoch:
        reset_epoch = validate_epoch(reset_epoch)
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
        # ALL DEFERRED restores Django's default only while no constraint is Deferrable.IMMEDIATE.
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        connection.ops.execute_sql_flush(statements)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    else:
        connection.ops.execute_sql_flush(statements)
    set_epoch(reset_epoch, scenario=scenario)
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


def _masked(value):
    """A volatile value: only whether it is set is compared."""
    return None if value in (None, "") else VOLATILE


def _canonical_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _masked(item) if key in VOLATILE_JSON_KEYS else _canonical_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        return round(value, 9)
    return value


def canonical_dump():
    result = {}
    for model in sorted(app_models(), key=lambda item: item._meta.db_table):
        volatile = VOLATILE_FIELDS.get(model._meta.model_name, set())
        always_masked = ALWAYS_MASKED_FIELDS.get(model._meta.model_name, set())
        rows = []
        for instance in model._default_manager.order_by("pk"):
            row = {}
            for field in model._meta.concrete_fields:
                if field.attname in always_masked:
                    row[field.attname] = VOLATILE
                    continue
                value = getattr(instance, field.attname)
                if field.attname in volatile:
                    row[field.attname] = _masked(value)
                    continue
                row[field.attname] = _canonical_value(value)
            rows.append(row)
        rows.sort(key=lambda row: json.dumps(row, sort_keys=True, default=str))
        result[model._meta.db_table] = rows
    return result


def canonical_checksum(dump):
    encoded = json.dumps(dump, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
