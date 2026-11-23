"""BL-34a (D-079): OES works discharge orders in MiniTOS priority order.

MiniTOS gives discharge orders rising priorities in vessel-slot sequence
(deck before hold, top tier first, row nearest the quay first). OES must pick
the next order by that priority, not by TOS order id or release time.
"""

import uuid
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import EnterpriseProjection, ExecutionOrder, SiteProjection
from .scenarios import next_dispatchable_order


class DischargePriorityOrderTests(TestCase):
    def setUp(self):
        enterprise = EnterpriseProjection.objects.create(
            source_id=uuid.uuid4(), code="enterprise-a", name="Enterprise A",
        )
        self.site = SiteProjection.objects.create(
            source_id=uuid.uuid4(), enterprise=enterprise,
            source_location_id=1, code="site-a", name="Site A",
            lifecycle_status="published", projection_status=SiteProjection.READY,
            source_revision=1, snapshot_checksum="a" * 64,
        )
        now = timezone.now()
        # Order ids and release times run against the priorities: the box on
        # the bottom tier has the lowest id and was released first.
        for tos_order_id, priority, released_at in (
            (1, 12, now), (2, 11, now + timedelta(seconds=1)), (3, 10, now + timedelta(seconds=2)),
        ):
            ExecutionOrder.objects.create(
                site=self.site, tos_order_id=tos_order_id, priority=priority,
                status=ExecutionOrder.RELEASED, released_at=released_at,
            )

    def test_next_dispatchable_order_is_lowest_priority(self):
        self.assertEqual(next_dispatchable_order().tos_order_id, 3)

    def test_default_ordering_is_priority_first(self):
        self.assertEqual(list(ExecutionOrder.objects.values_list("tos_order_id", flat=True)), [3, 2, 1])

    def test_finished_orders_are_skipped(self):
        ExecutionOrder.objects.filter(tos_order_id=3).update(status=ExecutionOrder.COMPLETE)
        self.assertEqual(next_dispatchable_order().tos_order_id, 2)
