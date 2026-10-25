"""Acceptance rules D-014 and D-016 (vault '03 Decisions', Yael, 2026-10-25).

Only the parts that current OES code can exercise are here. Seeded tie-breaks,
the simulation clock and the multi-CHE QC/TT/RTG hand-off do not exist yet; see
'Acceptance rules D-012..D-016' in the QA test plan.
Set SCMS_CONTRACT_STRICT=1 to drop the expectedFailure markers.
"""

import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import test_contract_execution_events as contract
from .models import EquipmentState, ExecutionOperation
from .services.dispatch import apply_command_event, dispatch_next_operation
from .services.ingestion import ingest_released_order

LEG_STEPS = [
    ("acknowledged", {}),
    ("started", {}),
    ("container_picked_up", {}),
    ("container_dropped", {"position_id": 112}),
    ("completed", {}),
]


class _Rollback(Exception):
    pass


class AcceptanceRuleTests(contract.ExecutionEventContractTests):
    # Reuse the fixture only; the contract tests run in their own module.
    test_producer_matches_golden_fixtures = None
    test_every_execution_event_carries_terminal_id = None
    test_produced_events_validate_against_contract_schema = None

    def release(self, order_id):
        return ingest_released_order(
            f"scms/v2/enterprise-a/site-a/tos/orders/{order_id}/released",
            self.released_order_payload(order_id, order_id * 100 + 1),
        )

    def run_leg(self, order, equipment=None):
        assignment = dispatch_next_operation(
            order.pk, equipment_id=equipment.pk if equipment else None,
        )
        command = assignment.commands.get()
        for event, kwargs in LEG_STEPS:
            apply_command_event(command.command_id, event, **kwargs)
        return command

    def snapshot(self, order, command):
        order.refresh_from_db()
        command.refresh_from_db()
        che = EquipmentState.objects.get(pk=self.equipment.pk)
        operation = ExecutionOperation.objects.get(order=order)
        return (
            order.status, operation.status, command.status,
            che.working, che.is_dispatched, che.is_available,
        )

    # D-016 -------------------------------------------------------------
    def test_d016_rerun_produces_same_state_sequence_not_same_messages(self):
        """D-016: rerunning the same order yields the same states in the same order.

        Given order 41 released to OES and one available CHE,
        When the same leg is run twice from the same starting state,
        Then the sequence of (order, operation, command, CHE) states and the
        execution event names are identical, while the message bytes are not
        (event_id and occurred_at differ), which D-016 allows.
        """
        runs = []
        for _ in range(2):
            try:
                with transaction.atomic():
                    assignment = dispatch_next_operation(self.order.pk)
                    command = assignment.commands.get()
                    states = [self.snapshot(self.order, command)]
                    for event, kwargs in LEG_STEPS:
                        apply_command_event(command.command_id, event, **kwargs)
                        states.append(self.snapshot(self.order, command))
                    produced = [
                        json.loads(m["payload"]) for m in self.publish_and_capture()
                        if isinstance(m["payload"], str)
                    ]
                    produced = [
                        p for p in produced if p.get("schema") == contract.EXECUTION_SCHEMA
                    ]
                    runs.append({
                        "states": states,
                        "events": [p["data"]["event"] for p in produced],
                        "event_ids": [p["event_id"] for p in produced],
                    })
                    raise _Rollback
            except _Rollback:
                pass
        first, second = runs
        self.assertEqual(len(first["events"]), 5)
        self.assertEqual(first["states"], second["states"])
        self.assertEqual(first["events"], second["events"])
        self.assertNotEqual(first["event_ids"], second["event_ids"])

    # D-014 -------------------------------------------------------------
    # Formerly known bug D-014; fixed by TCL-003a (paired hand-off dispatch).
    def test_d014_dispatch_picks_the_longest_idle_che(self):
        """D-014: pick the TT that has been without orders the longest.

        Given two available CHEs, where CHE 18 finished its last order before CHE 17,
        and CHE 17's latest telemetry is older than CHE 18's (both fresh),
        When a new order is dispatched without naming a CHE,
        Then it goes to CHE 18.
        """
        che_17 = self.equipment
        che_18 = EquipmentState.objects.create(
            site=self.site, tos_che_id=18, name="SC02",
            equipment_type_id=4, equipment_type_code="SC",
            powered_on=True, is_available=True,
            latitude=17.94, longitude=-102.15,
            source_timestamp=timezone.now(), last_seen_at=timezone.now(),
            simulation_session=self.session, sparkplug_device_id="CHE_18",
            device_connected=True, data_valid=True,
        )
        first = self.run_leg(self.order, equipment=che_18)
        second = self.run_leg(self.release(42), equipment=che_17)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertLess(first.completed_at, second.completed_at)

        now = timezone.now()
        EquipmentState.objects.filter(pk=che_17.pk).update(last_seen_at=now - timedelta(seconds=10))
        EquipmentState.objects.filter(pk=che_18.pk).update(last_seen_at=now - timedelta(seconds=2))

        assignment = dispatch_next_operation(self.release(43).pk)
        chosen = assignment.equipment
        self.assertEqual(
            chosen.tos_che_id, 18,
            f"dispatched to CHE {chosen.tos_che_id}; CHE 18 has been without orders longest",
        )
