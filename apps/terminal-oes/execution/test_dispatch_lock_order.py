"""OES deadlock fix: ``dispatch_next_operation`` locks the session before the order.

CI run 45142226732 (attempt 1, PR #67, s1-slice step 4, the Q18 TT→RTG dispatch)
returned ``500 deadlock detected ... while locking tuple in relation
"execution_simulationsession"``. Two transactions took the same two row locks in
opposite orders:

* Sparkplug ingest (``process_sparkplug_message``) locks the ``SimulationSession``
  first, then the result's ``EquipmentCommand`` with ``select_related`` (Postgres
  ``FOR UPDATE`` also locks the joined assignment, operation, order and CHE rows);
* ``dispatch_next_operation`` locked the order first and the session last.

The lock hierarchy is now session, then order, operations, CHE. The first test
pins the request order on any database; the second reproduces the race on
Postgres (skipped elsewhere, like CI's SQLite unit run).
"""

import threading
import time
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.db.models.query import QuerySet
from django.test import TransactionTestCase, skipUnlessDBFeature

from .models import (
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    SimulationSession,
)
from .services.dispatch import dispatch_next_operation
from .test_handoffs import HandoffTestBase


class RecordedLocks:
    """Record which models ``select_for_update()`` is requested on, in order."""

    def __init__(self):
        self.models = []

    def __enter__(self):
        original = QuerySet.select_for_update
        recorded = self.models

        def recording(queryset, *args, **kwargs):
            recorded.append(queryset.model)
            return original(queryset, *args, **kwargs)

        self._patch = mock.patch.object(QuerySet, "select_for_update", recording)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()

    def first_index(self, model):
        return self.models.index(model)


class DispatchLockOrderTests(HandoffTestBase):
    def assert_session_is_locked_first(self, locks):
        self.assertIn(SimulationSession, locks.models)
        self.assertEqual(locks.models[0], SimulationSession)
        for later in (ExecutionOrder, ExecutionOperation, EquipmentState):
            self.assertIn(later, locks.models)
            self.assertLess(locks.first_index(SimulationSession), locks.first_index(later))

    def test_first_dispatch_locks_the_session_before_the_order(self):
        with RecordedLocks() as locks:
            dispatch_next_operation(self.order.pk)
        self.assert_session_is_locked_first(locks)

    def test_chained_q18_dispatch_locks_the_session_before_the_order(self):
        # The step that deadlocked in CI: TT→RTG after QC→TT transferred.
        qc_cmd, _tt, tt_cmd = self.run_h1()
        self.transfer_h1(qc_cmd, tt_cmd)
        with RecordedLocks() as locks:
            dispatch_next_operation(self.order.pk)
        self.assert_session_is_locked_first(locks)

    def test_a_refusal_still_follows_the_old_precedence(self):
        # Order state first, then the plan, then the missing simulation.
        SimulationSession.objects.update(node_connected=False)
        ExecutionOrder.objects.filter(pk=self.order.pk).update(status=ExecutionOrder.COMPLETE)
        with self.assertRaises(ValidationError) as caught:
            dispatch_next_operation(self.order.pk)
        self.assertEqual(caught.exception.code, "order_not_released")

        ExecutionOrder.objects.filter(pk=self.order.pk).update(status=ExecutionOrder.RELEASED)
        self.order.operations.update(status=ExecutionOperation.COMPLETE)
        with self.assertRaises(ValidationError) as caught:
            dispatch_next_operation(self.order.pk)
        self.assertEqual(caught.exception.code, "nothing_to_dispatch")

        self.order.operations.update(status=ExecutionOperation.PLANNED)
        with self.assertRaises(ValidationError) as caught:
            dispatch_next_operation(self.order.pk)
        self.assertEqual(caught.exception.code, "no_simulation")
        self.assertFalse(EquipmentCommand.objects.exists())

    def test_unknown_order_is_still_does_not_exist(self):
        with self.assertRaises(ExecutionOrder.DoesNotExist):
            dispatch_next_operation(999_999)


@skipUnlessDBFeature("has_select_for_update")
class DispatchVersusResultIngestRaceTests(TransactionTestCase):
    """Real concurrency on Postgres: a DDATA-style lock sequence vs the Q18 dispatch."""

    tt_ids = HandoffTestBase.tt_ids
    setUp = HandoffTestBase.setUp
    che = HandoffTestBase.che
    release = HandoffTestBase.release
    result = HandoffTestBase.result
    command_for = HandoffTestBase.command_for
    chosen_tt = HandoffTestBase.chosen_tt
    run_h1 = HandoffTestBase.run_h1
    transfer_h1 = HandoffTestBase.transfer_h1

    def waiting_on_a_lock(self):
        with connection.cursor() as cursor:
            # pg_stat_activity is cached per transaction; this one runs inside one.
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                " AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            )
            return cursor.fetchone()[0]

    def test_ingest_lock_sequence_and_q18_dispatch_do_not_deadlock(self):
        if connection.vendor != "postgresql":
            self.skipTest("needs Postgres row locks to interleave two transactions")
        with transaction.atomic():  # results are applied inside process_sparkplug_message
            qc_cmd, _tt, tt_cmd = self.run_h1()
            self.transfer_h1(qc_cmd, tt_cmd)
        errors = {}
        session_locked = threading.Event()

        def ingest():
            # What process_sparkplug_message does for a result on this order:
            # session first, then the command with its joined rows.
            try:
                with transaction.atomic():
                    SimulationSession.objects.select_for_update().get(pk=self.session.pk)
                    session_locked.set()
                    # Wait until the dispatch is blocked behind the session lock.
                    deadline = time.monotonic() + 10
                    while not self.waiting_on_a_lock():
                        if time.monotonic() > deadline:
                            raise AssertionError("the dispatch never waited on the session lock")
                        time.sleep(0.05)
                    EquipmentCommand.objects.select_for_update().select_related(
                        "assignment__operation__order", "assignment__equipment",
                    ).get(pk=qc_cmd.pk)
            except Exception as exc:  # noqa: BLE001 - reported by the assertion below
                errors["ingest"] = exc
            finally:
                connections.close_all()

        def dispatch():
            try:
                session_locked.wait(10)
                dispatch_next_operation(self.order.pk)
            except Exception as exc:  # noqa: BLE001
                errors["dispatch"] = exc
            finally:
                connections.close_all()

        threads = [threading.Thread(target=ingest), threading.Thread(target=dispatch)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertFalse(any(thread.is_alive() for thread in threads), "a thread hung")
        self.assertEqual(errors, {}, errors)
        self.assertEqual(
            EquipmentCommand.objects.filter(assignment__equipment=self.rtg).count(), 1,
        )
