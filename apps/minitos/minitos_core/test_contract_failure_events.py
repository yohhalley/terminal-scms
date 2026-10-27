"""TCL-005 consumer-side contract tests for ``leg_failed`` with and without the
optional ``data.detail`` (TCL-028 §3.2c).

The goldens under ``<repo>/contracts/golden/scms.execution.event.v1/failure/``
are written by the OES producer test
(``apps/terminal-oes/execution/test_contract_failure_events.py``). Here
``leg_dispatched`` and ``leg_started`` from the one-leg goldens, then the
``leg_failed`` golden, are fed through the real ``apply_execution_message``.
Only id values are rebound; keys are never added or removed.
"""

import json
import unittest

from django.test import override_settings

from minitos_core.models import IntegrationInbox, Order, OrderLeg
from minitos_core.services import apply_execution_message
from minitos_core import test_contract_execution_events as one_leg
from minitos_core.test_contract_execution_events import GOLDEN_DIR

FAILURE_DIR = GOLDEN_DIR / 'failure'


def load(path):
    if not path.exists():
        raise unittest.SkipTest(f'{path} is not available (app-only Docker image)')
    return json.loads(path.read_text())


@override_settings(MES_API_TOKEN='test-mes-token')
class GoldenLegFailedConsumerTests(one_leg.GoldenExecutionEventConsumerTests):
    # Base class imported via its module so the loader does not run it twice here.
    # Don't re-run the inherited one-leg consumer tests in this class.
    test_golden_fixtures_cover_one_full_leg = None
    test_golden_envelopes_carry_terminal_id = None
    test_golden_leg_applies_through_real_consumer = None

    def replay_failure(self, failure_name):
        documents = [
            ('01_leg_dispatched.json', load(GOLDEN_DIR / '01_leg_dispatched.json')),
            ('02_leg_started.json', load(GOLDEN_DIR / '02_leg_started.json')),
            (failure_name, load(FAILURE_DIR / failure_name)),
        ]
        for name, document in documents:
            topic, raw = self.rebind(document)
            try:
                apply_execution_message(topic, raw)
            except Exception as exc:  # report which fixture failed and why
                self.fail(f'{name} rejected by apply_execution_message: {exc}')
        self.order.refresh_from_db()
        self.leg.refresh_from_db()
        self.assertEqual(self.leg.status, OrderLeg.FAILED)
        self.assertEqual(self.order.status, Order.FAILED)
        self.assertEqual(
            IntegrationInbox.objects.filter(
                source='terminal-oes', status=IntegrationInbox.PROCESSED,
            ).count(),
            len(documents),
        )
        return documents[-1][1]

    def test_leg_failed_with_detail_is_accepted(self):
        document = self.replay_failure('01_leg_failed_with_detail.json')
        self.assertIn('detail', document['payload']['data'])

    def test_leg_failed_without_detail_is_accepted(self):
        document = self.replay_failure('02_leg_failed_without_detail.json')
        self.assertNotIn('detail', document['payload']['data'])
