"""QA M2 (PR #24 review): after ``make scenario-reset`` OES accepts no MiniTOS
message from the previous run.

Reproduces finding O2: after a reset the Site uuid5, the Location pk and the
order ids are the same again and the OES inbox is empty, so run 1's
``order.released`` (queued in the persistent QoS 1 session, or published from
an old MiniTOS outbox row because the Makefile wipes OES first) used to be a
valid new message. With the reset epoch both services got from the same
``make scenario-reset``, ``process_message`` ignores it.

Messages go through ``process_message`` (what ``run_mqtt_consumer`` calls) as
raw JSON bytes; only the HTTP snapshot fetch is replaced.
"""

import io
import json
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import ExecutionOrder, InboxEvent, OutboxEvent, ScenarioResetEpoch, SiteProjection
from .scenarios import canonical_checksum, canonical_dump, reset_execution_tables
from .services.events import queue_execution_event
from .services.messages import process_message
from .services.reset_epoch import current_epoch, ignored_count
from .test_contract_execution_events import SCHEMA_PATH, check_schema
from .test_scenario_reset import fresh_events, load_fixture

RUN_1 = "20261102T080000Z-0000aaaa"
RUN_2 = "20261102T090000Z-0000bbbb"
LOGGER = "execution.services.reset_epoch"


def deliver(topic, payload):
    return process_message(topic, json.dumps(payload).encode("utf-8"))


def minitos_run(epoch, document=None):
    """The events one MiniTOS reset publishes, stamped with its epoch."""
    document = document or load_fixture()
    events = fresh_events(document)
    for event in events.values():
        if epoch:
            event["payload"]["reset_epoch"] = epoch
        else:
            event["payload"].pop("reset_epoch", None)
    return document, events


def project(document, events):
    with mock.patch(
        "execution.services.site_sync.fetch_execution_snapshot",
        return_value=document["snapshot_document"],
    ):
        deliver(**events["scms.site.created.v1"])
        return deliver(**events["scms.site.configuration.published.v1"])


class StaleMiniTOSMessageAfterResetTests(TestCase):
    def setUp(self):
        # Run 1: reset, project, ingest the released order.
        reset_execution_tables(reset_epoch=RUN_1, scenario="import_v1")
        document, run1 = minitos_run(RUN_1)
        project(document, run1)
        self.stale_released = run1["scms.order.released.v1"]
        self.assertIsInstance(deliver(**self.stale_released), ExecutionOrder)
        self.stale_published = run1["scms.site.configuration.published.v1"]

        # Run 2: reset with a new epoch and project the new run's site only.
        reset_execution_tables(reset_epoch=RUN_2, scenario="import_v1")
        self.document, self.run2 = minitos_run(RUN_2)
        site = project(self.document, self.run2)
        self.assertEqual(site.projection_status, SiteProjection.READY)
        self.clean_checksum = canonical_checksum(canonical_dump())

    def test_previous_runs_order_released_is_ignored(self):
        """O2: run 1's order.released after the reset creates no order."""
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.assertIsNone(deliver(**self.stale_released))
        self.assertIn(f"reset_epoch seen={RUN_1} stored={RUN_2}", logs.output[0])
        self.assertFalse(ExecutionOrder.objects.exists())
        self.assertFalse(
            InboxEvent.objects.filter(event_id=self.stale_released["payload"]["event_id"]).exists()
        )
        self.assertEqual(canonical_checksum(canonical_dump()), self.clean_checksum)

    def test_previous_runs_message_is_ignored_before_the_new_run_publishes(self):
        """Makefile order: OES is wiped while MiniTOS still has run 1's outbox rows."""
        reset_execution_tables(reset_epoch=RUN_2, scenario="import_v1")
        empty = canonical_checksum(canonical_dump())
        with self.assertLogs(LOGGER, "INFO"):
            self.assertIsNone(deliver(**self.stale_released))
        self.assertEqual(canonical_checksum(canonical_dump()), empty)

    def test_stale_higher_revision_publish_cannot_skip_the_new_revision(self):
        """A run-1 revision-2 publish must not make OES ignore run 2's revision 1."""
        reset_execution_tables(reset_epoch=RUN_2, scenario="import_v1")
        stale = json.loads(json.dumps(self.stale_published))
        stale["payload"]["configuration_revision"] = 2
        stale["payload"]["site"]["configuration_revision"] = 2
        with mock.patch("execution.services.site_sync.fetch_execution_snapshot") as fetch:
            with self.assertLogs(LOGGER, "INFO"):
                self.assertIsNone(deliver(**stale))
            fetch.assert_not_called()
        self.assertFalse(SiteProjection.objects.exists())
        site = project(self.document, self.run2)
        self.assertEqual((site.source_revision, site.projection_status), (1, SiteProjection.READY))

    def test_unstamped_message_is_ignored_while_a_fence_is_set(self):
        _, unstamped = minitos_run(None)
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.assertIsNone(deliver(**unstamped["scms.order.released.v1"]))
        self.assertIn(f"reset_epoch seen=none stored={RUN_2}", logs.output[0])
        self.assertFalse(ExecutionOrder.objects.exists())

    def test_ignored_messages_are_logged_at_info_and_counted(self):
        """TL 2026-11-02: drops are visible (INFO log with type and epochs) and counted."""
        before = ignored_count()
        before_released = ignored_count("scms.order.released.v1")
        before_published = ignored_count("scms.site.configuration.published.v1")
        _, unstamped = minitos_run(None)
        with self.assertLogs(LOGGER, "INFO") as logs:
            deliver(**self.stale_released)
            deliver(**self.stale_published)
            deliver(**unstamped["scms.order.released.v1"])
        self.assertEqual([record.levelname for record in logs.records], ["INFO"] * 3)
        self.assertIn("type=scms.order.released.v1", logs.output[0])
        self.assertIn(f"reset_epoch seen={RUN_1} stored={RUN_2}", logs.output[0])
        self.assertIn("type=scms.site.configuration.published.v1", logs.output[1])
        self.assertIn(f"reset_epoch seen=none stored={RUN_2}", logs.output[2])
        self.assertIn(f"ignored_total={before + 3}", logs.output[2])
        self.assertEqual(ignored_count(), before + 3)
        self.assertEqual(ignored_count("scms.order.released.v1"), before_released + 2)
        self.assertEqual(
            ignored_count("scms.site.configuration.published.v1"), before_published + 1,
        )

        # Accepted messages are not counted.
        deliver(**self.run2["scms.order.released.v1"])
        self.assertEqual(ignored_count(), before + 3)

    def test_current_runs_order_released_is_accepted(self):
        order = deliver(**self.run2["scms.order.released.v1"])
        self.assertIsInstance(order, ExecutionOrder)
        self.assertEqual(ExecutionOrder.objects.count(), 1)


class UnfencedResetStillAcceptsStaleMessagesTests(TestCase):
    def test_reset_without_epoch_accepts_the_previous_runs_order(self):
        """Characterisation: without --reset-epoch (no fence) the O2 hazard remains."""
        reset_execution_tables()
        document, run1 = minitos_run(None)
        project(document, run1)
        stale = run1["scms.order.released.v1"]
        reset_execution_tables()
        _, run2 = minitos_run(None)
        project(document, run2)
        self.assertIsInstance(deliver(**stale), ExecutionOrder)


class ResetEpochProducerTests(TestCase):
    def released_order(self, epoch):
        reset_execution_tables(reset_epoch=epoch, scenario="import_v1")
        document, events = minitos_run(epoch)
        project(document, events)
        return deliver(**events["scms.order.released.v1"])

    def test_execution_events_carry_the_epoch_and_validate(self):
        order = self.released_order(RUN_2)
        event = queue_execution_event(order, "leg_dispatched", operation=order.operations.first())
        self.assertEqual(event.payload["reset_epoch"], RUN_2)
        if not SCHEMA_PATH.exists():
            self.skipTest(f"{SCHEMA_PATH} is not available (app-only Docker image)")
        schema = json.loads(SCHEMA_PATH.read_text())
        self.assertIn("reset_epoch", schema["properties"])
        payload = dict(event.payload, event_id=str(event.event_id))
        self.assertEqual(check_schema(payload, schema), [])

    def test_no_epoch_no_stamp(self):
        order = self.released_order(None)
        event = queue_execution_event(order, "leg_dispatched", operation=order.operations.first())
        self.assertNotIn("reset_epoch", event.payload)

    def test_epoch_is_masked_in_the_canonical_dump(self):
        reset_execution_tables(reset_epoch=RUN_1)
        first = canonical_checksum(canonical_dump())
        reset_execution_tables(reset_epoch=RUN_2)
        self.assertEqual(canonical_checksum(canonical_dump()), first)

    def test_reset_stores_one_epoch(self):
        reset_execution_tables(reset_epoch=RUN_1)
        reset_execution_tables(reset_epoch=RUN_2, scenario="import_v1")
        self.assertEqual(list(ScenarioResetEpoch.objects.values_list("epoch", "scenario")), [(RUN_2, "import_v1")])
        reset_execution_tables()
        self.assertEqual(current_epoch(), "")

    def test_invalid_epoch_is_refused_before_wiping(self):
        document, events = minitos_run(None)
        project(document, events)
        with self.assertRaises(ValidationError):
            reset_execution_tables(reset_epoch="bad epoch; rm")
        self.assertTrue(SiteProjection.objects.exists())

    def test_command_passes_the_epoch(self):
        out = io.StringIO()
        call_command("scenario_reset", "import_v1", "--noinput", "--reset-epoch", RUN_2, stdout=out)
        self.assertEqual(current_epoch(), RUN_2)
        self.assertIn(f"reset epoch {RUN_2}", out.getvalue())
        with self.assertRaises(CommandError):
            call_command("scenario_reset", "import_v1", "--noinput", "--reset-epoch", "-", stdout=io.StringIO())
        self.assertEqual(current_epoch(), RUN_2)
        self.assertFalse(OutboxEvent.objects.exists())
