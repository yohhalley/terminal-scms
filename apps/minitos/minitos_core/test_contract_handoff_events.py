"""TCL-005 consumer-side contract tests for the TCL-003a hand-off events.

Feeds ``contracts/golden/scms.execution.event.v1/handoff/*.json`` (written by the
OES producer test ``execution/test_contract_handoff_events.py``) through the real
MiniTOS consumer ``apply_execution_message``. As in
``test_contract_execution_events``, only id *values* are rebound to this database;
keys are never added or removed.

The scenario (QC01 / TT / RTG01, vessel import, BLOCK01) comes from TCL-003a's
``test_handoff_execution.HandoffExecutionTests``; before TCL-003a is merged this
module is skipped.
"""

import json
import unittest
from pathlib import Path

from minitos_core.models import ContainerVisit, IntegrationInbox, Order, OrderLeg, Position
from minitos_core.services import apply_execution_message
from minitos_core.services import order_execution

try:
    from minitos_core import test_handoff_execution as handoff_tests
except ImportError as exc:  # main before TCL-003a
    raise unittest.SkipTest(f'TCL-003a hand-off consumer not present: {exc}')

REPO_ROOT = Path(__file__).resolve().parents[3]
HANDOFF_GOLDEN_DIR = (
    REPO_ROOT / 'contracts' / 'golden' / 'scms.execution.event.v1' / 'handoff'
)
# OES ids in the golden scenario (execution/test_handoffs.py HandoffTestBase).
OES_ORDER, OES_LEGS = 41, (4101, 4102, 4103)
OES_QC, OES_TTS, OES_RTG = 1, (11, 12), 21
OES_DESTINATION_POSITION = 112


def load(name):
    if not HANDOFF_GOLDEN_DIR.is_dir():
        raise unittest.SkipTest(f'{HANDOFF_GOLDEN_DIR} is not available (app-only Docker image)')
    return json.loads((HANDOFF_GOLDEN_DIR / f'{name}.json').read_text())


class GoldenHandoffEventConsumerTests(handoff_tests.HandoffExecutionTests):
    def rebind(self, document):
        payload = json.loads(json.dumps(document['payload']))
        data = payload['data']
        che = {OES_QC: self.qc.pk, OES_RTG: self.rtg.pk}
        che.update({tt: self.tt.pk for tt in OES_TTS})
        leg = {oes: local.pk for oes, local in zip(OES_LEGS, self.legs)}
        for key in ('che_id', 'from_che_id', 'to_che_id'):
            if key in data:
                data[key] = che[data[key]]
        for key in ('leg_id', 'giver_leg_id', 'receiver_leg_id'):
            if key in data:
                data[key] = leg[data[key]]
        if 'cancelled_leg_ids' in data:
            data['cancelled_leg_ids'] = [leg[value] for value in data['cancelled_leg_ids']]
        if 'container_visit_id' in data:  # consumer rejects a foreign visit (mismatch check)
            data['container_visit_id'] = self.visit.pk
        if data.get('destination_position_id') == OES_DESTINATION_POSITION:
            data['destination_position_id'] = self.planned.pk
        self.assertEqual(data['order_id'], OES_ORDER)
        data['order_id'] = self.order.pk
        payload['site']['source_location_id'] = self.location.pk
        payload['terminal']['id'] = self.location.pk
        topic = document['topic'].replace(f'/orders/{OES_ORDER}/', f'/orders/{self.order.pk}/')
        return topic, json.dumps(payload, separators=(',', ':'))

    def apply(self, name):
        topic, raw = self.rebind(load(name))
        try:
            apply_execution_message(topic, raw)
        except Exception as exc:
            self.fail(f'{name} rejected by apply_execution_message: {exc}')

    def test_golden_transfers_move_custody_qc_tt_rtg(self):
        self.event('container_picked_up', che=self.qc, source='VESSEL')
        self.apply('01_container_transferred_qc_to_tt')
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.current_che, self.tt)
        self.apply('02_container_transferred_tt_to_rtg')
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.current_che, self.rtg)
        self.assertEqual(
            IntegrationInbox.objects.filter(status=IntegrationInbox.PROCESSED).count(), 2,
        )

    def test_golden_no_tt_timeout_relocates_to_block01_and_closes_the_order(self):
        self.event('container_picked_up', che=self.qc, source='VESSEL')
        self.apply('03_handoff_exception_no_tt_timeout')
        self.visit.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.visit.status, ContainerVisit.AT_YARD)
        self.assertEqual(self.visit.exception_reason, 'NO_TT_TIMEOUT')
        self.assertEqual(Position.objects.get(container_visit=self.visit), self.planned)
        # A-1 (TL interim call, pending Yael): no-show completes the order by
        # exception; follows MiniTOS NO_SHOW_ORDER_STATUS if that decision flips.
        self.assertEqual(
            self.order.status,
            getattr(order_execution, 'NO_SHOW_ORDER_STATUS', Order.CANCELLED),
        )
        self.assertEqual(
            set(OrderLeg.objects.filter(order=self.order).values_list('status', flat=True)),
            {OrderLeg.CANCELLED},
        )


# Do not re-run TCL-003a's own tests from this module.
for _name in dir(handoff_tests.HandoffExecutionTests):
    if _name.startswith('test_') and not _name.startswith('test_golden_'):
        setattr(GoldenHandoffEventConsumerTests, _name, None)
