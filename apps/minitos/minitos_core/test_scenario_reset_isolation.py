"""QA M2: after ``make scenario-reset`` no message from the previous run is accepted.

Reproduces QA probe P5 from the PR #24 review
(/workspace/qa/reviews/TCL-006-PR24-review.md, M2): the previous run's OES
``scms.execution.event.v1`` ``leg_dispatched`` for order 1 arrives after the
reset and the release of the new order 1. Ids restart at 1 and the Site uuid5
is the same, and the inbox is empty after the reset, so before M2 MiniTOS
accepted it and dispatched the new run's leg 1 and QC01.

The fix is a reset epoch (run generation marker): ``make scenario-reset`` gives
both services the same fresh ``--reset-epoch``; envelopes carry it as the
top-level ``reset_epoch`` key and consumers ignore any other value.
"""

import io
import json
import uuid

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from minitos_core.models import (
    Che, IntegrationInbox, IntegrationOutbox, Order, OrderLeg, ScenarioResetEpoch,
)
from minitos_core.scenarios import reset_scenario, run_step
from minitos_core.scenarios.base import canonical_checksum, canonical_dump
from minitos_core.services import apply_execution_message
from minitos_core.services.reset_epoch import current_epoch, ignored_count, stale_reason

RUN_1 = '20261102T080000Z-0000aaaa'
RUN_2 = '20261102T090000Z-0000bbbb'


def oes_leg_dispatched(reset_epoch):
    """The OES envelope for ``leg_dispatched`` of order 1 / leg 1 on QC01.

    Same shape as the real OES producer (contracts/golden/
    scms.execution.event.v1/01_leg_dispatched.json), bound to the current
    import_v1 ids, and stamped with ``reset_epoch`` the way OES stamps it in
    the run it was produced in (``None``: an unfenced, pre-M2 envelope).
    """
    order = Order.objects.select_related('work_queue__location').get(pk=1)
    leg = OrderLeg.objects.filter(order=order).order_by('sequence').first()
    location = order.work_queue.location
    site = location.site
    qc = Che.objects.get(name='QC01')
    payload = {
        'schema': 'scms.execution.event.v1',
        'event_id': str(uuid.uuid4()),
        'enterprise': {
            'id': str(site.enterprise_id), 'code': site.enterprise.code,
            'name': site.enterprise.name,
        },
        'site': {
            'id': str(site.pk), 'code': site.code, 'name': location.name,
            'source_location_id': location.pk, 'configuration_revision': 1,
        },
        'terminal': {'id': location.pk, 'code': site.code},
        'data': {
            'event': 'leg_dispatched', 'order_id': order.pk, 'leg_id': leg.pk,
            'che_id': qc.pk, 'che_name': qc.name,
            'occurred_at': '2026-10-25T00:00:05+00:00',
        },
    }
    if reset_epoch is not None:
        payload['reset_epoch'] = reset_epoch
    topic = (
        f'scms/v2/{site.enterprise.code}/{site.code}/oes/orders/{order.pk}/execution'
    )
    return topic, payload


LOGGER = 'minitos_core.services.reset_epoch'


def deliver(topic, payload):
    """What run_uns_consumer does with an MQTT message: raw JSON bytes."""
    return apply_execution_message(topic, json.dumps(payload).encode('utf-8'))


def new_run_state():
    leg = OrderLeg.objects.filter(order_id=1).order_by('sequence').first()
    qc = Che.objects.get(name='QC01')
    return leg.status, leg.che_id, qc.is_dispatched


class StaleExecutionEventAfterResetTests(TestCase):
    """P5: a previous-run OES event is ignored once the reset has a new epoch."""

    def setUp(self):
        # Run 1: seed, release, and capture the leg_dispatched OES sent in run 1
        # (still queued for the MiniTOS consumer, or in flight, at reset time).
        reset_scenario('import_v1', reset_epoch=RUN_1)
        run_step('import_v1', 'release-discharge')
        self.stale_topic, self.stale = oes_leg_dispatched(RUN_1)

        # make scenario-reset (run 2), then the demo releases the new order 1.
        reset_scenario('import_v1', reset_epoch=RUN_2)
        self.clean = canonical_checksum(canonical_dump())
        run_step('import_v1', 'release-discharge')
        self.released = canonical_checksum(canonical_dump())
        self.before = new_run_state()

    def test_stale_leg_dispatched_from_the_previous_run_is_ignored(self):
        self.assertEqual(self.stale['data']['order_id'], 1)  # same ids as run 2
        with self.assertLogs(LOGGER, 'INFO') as logs:
            result = deliver(self.stale_topic, self.stale)

        self.assertIsNone(result)
        self.assertIn(f"reset_epoch seen={RUN_1} stored={RUN_2}", logs.output[0])
        self.assertEqual(new_run_state(), self.before)
        self.assertEqual(self.before[2], False)  # QC01 not dispatched
        self.assertFalse(IntegrationInbox.objects.exists())
        self.assertEqual(canonical_checksum(canonical_dump()), self.released)

    def test_stale_event_is_ignored_even_when_it_arrives_before_the_release(self):
        reset_scenario('import_v1', reset_epoch=RUN_2)
        with self.assertLogs(LOGGER, 'INFO'):
            self.assertIsNone(deliver(self.stale_topic, self.stale))
        self.assertEqual(canonical_checksum(canonical_dump()), self.clean)

    def test_unfenced_envelope_is_ignored_while_an_epoch_is_set(self):
        topic, payload = oes_leg_dispatched(None)
        with self.assertLogs(LOGGER, 'INFO'):
            self.assertIsNone(deliver(topic, payload))
        self.assertEqual(new_run_state(), self.before)
        self.assertIn('no reset_epoch', stale_reason(payload))

    def test_ignored_message_is_logged_at_info_and_counted(self):
        """TL 2026-11-02: drops are visible (INFO log with type and epochs) and counted."""
        schema = 'scms.execution.event.v1'
        before, before_type = ignored_count(), ignored_count(schema)
        with self.assertLogs(LOGGER, 'INFO') as logs:
            deliver(self.stale_topic, self.stale)
            deliver(*oes_leg_dispatched(None))
        self.assertEqual([record.levelname for record in logs.records], ['INFO', 'INFO'])
        self.assertIn(f'type={schema}', logs.output[0])
        self.assertIn(f'reset_epoch seen={RUN_1} stored={RUN_2}', logs.output[0])
        self.assertIn(f'reset_epoch seen=none stored={RUN_2}', logs.output[1])
        self.assertIn(f'ignored_total={before + 2}', logs.output[1])
        self.assertEqual(ignored_count(), before + 2)
        self.assertEqual(ignored_count(schema), before_type + 2)

        # Accepted messages are not counted.
        deliver(*oes_leg_dispatched(RUN_2))
        self.assertEqual(ignored_count(), before + 2)

    def test_the_same_event_from_the_current_run_is_applied(self):
        """Control: the fence only drops other runs; the new run keeps working."""
        topic, payload = oes_leg_dispatched(RUN_2)
        order = deliver(topic, payload)

        self.assertEqual(order.pk, 1)
        status, che_id, qc_dispatched = new_run_state()
        self.assertEqual(status, OrderLeg.DISPATCHED)
        self.assertEqual(che_id, Che.objects.get(name='QC01').pk)
        self.assertTrue(qc_dispatched)
        self.assertEqual(
            IntegrationInbox.objects.get().status, IntegrationInbox.PROCESSED,
        )


class UnfencedResetStillAcceptsStaleEventsTests(TestCase):
    """Why make scenario-reset must pass --reset-epoch: without it, P5 still happens."""

    def test_reset_without_epoch_accepts_the_previous_runs_event(self):
        reset_scenario('import_v1')
        run_step('import_v1', 'release-discharge')
        topic, stale = oes_leg_dispatched(None)

        reset_scenario('import_v1')
        run_step('import_v1', 'release-discharge')
        self.assertEqual(current_epoch(), '')
        deliver(topic, stale)

        status, _, qc_dispatched = new_run_state()
        self.assertEqual(status, OrderLeg.DISPATCHED)
        self.assertTrue(qc_dispatched)


class ResetEpochSeedTests(TestCase):
    def test_reset_stores_the_epoch_and_stamps_every_outbox_envelope(self):
        result = reset_scenario('import_v1', reset_epoch=RUN_2)
        run_step('import_v1', 'release-discharge')

        self.assertEqual(result['reset_epoch'], RUN_2)
        self.assertEqual(current_epoch(), RUN_2)
        self.assertEqual(ScenarioResetEpoch.objects.get().scenario, 'import_v1')
        schemas = set()
        for event in IntegrationOutbox.objects.all():
            schemas.add(event.schema)
            self.assertEqual(event.payload.get('reset_epoch'), RUN_2, event.schema)
        self.assertEqual(schemas, {
            'scms.site.created.v1', 'scms.site.configuration.published.v1',
            'scms.order.released.v1',
            # TCL-003b: the seeded visits' creation events (stamped too).
            'scms.visit.status_changed.v1',
        })

    def test_reset_without_epoch_stamps_nothing(self):
        reset_scenario('import_v1', reset_epoch=RUN_1)
        reset_scenario('import_v1')
        self.assertFalse(ScenarioResetEpoch.objects.exists())
        self.assertTrue(IntegrationOutbox.objects.exists())
        for event in IntegrationOutbox.objects.all():
            self.assertNotIn('reset_epoch', event.payload)

    def test_two_resets_with_different_epochs_have_the_same_canonical_state(self):
        reset_scenario('import_v1', reset_epoch=RUN_1)
        first = canonical_dump()
        reset_scenario('import_v1', reset_epoch=RUN_2)
        second = canonical_dump()
        for table in first:
            self.assertEqual(first[table], second[table], table)
        self.assertEqual(canonical_checksum(first), canonical_checksum(second))

    def test_invalid_epoch_is_refused_before_wiping(self):
        reset_scenario('import_v1', reset_epoch=RUN_1)
        before = canonical_checksum(canonical_dump())
        with self.assertRaises(ValidationError):
            reset_scenario('import_v1', reset_epoch='bad epoch; drop')
        self.assertEqual(current_epoch(), RUN_1)
        self.assertEqual(canonical_checksum(canonical_dump()), before)

    def test_command_passes_and_reports_the_epoch(self):
        out = io.StringIO()
        call_command(
            'scenario_reset', 'import_v1', '--noinput', '--reset-epoch', RUN_2, stdout=out,
        )
        self.assertIn(f'reset epoch {RUN_2}', out.getvalue())
        self.assertEqual(current_epoch(), RUN_2)

    def test_command_rejects_an_invalid_epoch(self):
        with self.assertRaisesMessage(CommandError, 'A reset epoch is 1-64 characters'):
            call_command(
                'scenario_reset', 'import_v1', '--noinput', '--reset-epoch', '../x',
                stdout=io.StringIO(),
            )
