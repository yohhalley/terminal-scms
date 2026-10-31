"""TCL-006: terminal-oes side of ``make scenario-reset`` / ``scenario-step``.

The fixture holds the exact events a fresh MiniTOS ``import_v1`` seed
publishes (site.created, configuration.published, order.released) plus the
execution snapshot MiniTOS serves over HTTP. The HTTP fetch is replaced by
``snapshot_loader``; no broker is involved.
"""

import io
import json
import uuid
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    DispatchAssignment,
    EquipmentState,
    ExecutionOrder,
    InboxEvent,
    SimulationSession,
    SiteProjection,
)
from .scenarios import (
    canonical_checksum,
    canonical_dump,
    reset_execution_tables,
    wait_for_ready_site,
)
from .services.ingestion import ingest_released_order
from .services.site_sync import ingest_site_configuration_published, ingest_site_created

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "scenario_import_v1_events.json"
# uuid5(TCL-006 namespace, "site:terminal-velocity:test-terminal") in MiniTOS.
IMPORT_V1_SITE_ID = uuid.UUID("2ae1455e-25a2-575c-88f3-d4b918b0def1")


def load_fixture():
    return json.loads(FIXTURE.read_text())


def fresh_events(document):
    """Each MiniTOS reset re-emits the events with new uuid4 ids and timestamps."""
    events = json.loads(json.dumps(document["events"]))
    now = timezone.now().isoformat()
    for event in events.values():
        event["payload"]["event_id"] = str(uuid.uuid4())
        event["payload"]["occurred_at"] = now
    return events


def project_import_v1(document=None):
    """Replay a MiniTOS import_v1 reset the way the OES MQTT consumer would."""
    document = document or load_fixture()
    events = fresh_events(document)
    created = events["scms.site.created.v1"]
    ingest_site_created(created["topic"], created["payload"])
    published = events["scms.site.configuration.published.v1"]
    return ingest_site_configuration_published(
        published["topic"], published["payload"],
        snapshot_loader=mock.Mock(return_value=document["snapshot_document"]),
    ), events


class ScenarioResetTests(TestCase):
    def test_fixture_is_the_import_v1_site(self):
        site, _ = project_import_v1()
        self.assertEqual(site.source_id, IMPORT_V1_SITE_ID)
        self.assertEqual((site.enterprise.code, site.code), ("terminal-velocity", "test-terminal"))
        self.assertEqual(site.projection_status, SiteProjection.READY)
        self.assertEqual(
            sorted(site.equipment.values_list("name", "equipment_type_code")),
            [("QC01", "STS"), ("RTG01", "RTG"), ("TT01", "TT"), ("TT02", "TT")],
        )

    def test_two_resets_produce_identical_canonical_state(self):
        reset_execution_tables()
        project_import_v1()
        first = canonical_dump()

        reset_execution_tables()
        project_import_v1()
        second = canonical_dump()

        for table in first:
            self.assertEqual(first[table], second[table], table)
        self.assertEqual(canonical_checksum(first), canonical_checksum(second))

    def test_without_reset_a_reseeded_minitos_revision_is_ignored(self):
        """Why OES must be wiped: MiniTOS restarts at revision 1 after its reset."""
        site, _ = project_import_v1()
        self.assertEqual(site.projection_status, SiteProjection.READY)
        # Replay a MiniTOS-only reset: site.created moves the projection back to
        # draft and the revision-1 publish is then skipped as stale.
        project_import_v1()
        site.refresh_from_db()
        self.assertNotEqual(site.projection_status, SiteProjection.READY)

        reset_execution_tables()
        site, _ = project_import_v1()
        self.assertEqual(site.projection_status, SiteProjection.READY)

    def test_reset_wipes_execution_tables_and_keeps_users(self):
        get_user_model().objects.create_user("operator", password="not-a-real-password")
        _, events = project_import_v1()
        released = events["scms.order.released.v1"]
        ingest_released_order(released["topic"], released["payload"])
        self.assertTrue(ExecutionOrder.objects.exists())

        out = io.StringIO()
        call_command("scenario_reset", "import_v1", "--noinput", stdout=out)

        self.assertFalse(SiteProjection.objects.exists())
        self.assertFalse(ExecutionOrder.objects.exists())
        self.assertFalse(InboxEvent.objects.exists())
        self.assertTrue(get_user_model().objects.filter(username="operator").exists())
        self.assertIn("wiped", out.getvalue())

    def test_reset_refuses_while_a_simulation_is_active(self):
        site, _ = project_import_v1()
        SimulationSession.objects.create(
            site=site, configuration_revision=1, configuration_checksum="a" * 64,
            sparkplug_group_id="TV", sparkplug_node_id="SCMS_TV_TEST",
            actual_state=SimulationSession.RUNNING,
        )
        with self.assertRaises(ValidationError):
            reset_execution_tables()
        with self.assertRaisesMessage(CommandError, "stop the simulation"):
            call_command("scenario_reset", "import_v1", "--noinput", stdout=io.StringIO())
        self.assertTrue(SiteProjection.objects.exists())

        call_command("scenario_reset", "import_v1", "--noinput", "--force", stdout=io.StringIO())
        self.assertFalse(SimulationSession.objects.exists())

    @override_settings(SIMULATOR_SIM_SETTINGS={"SIMULATOR_SEED": "7"})
    def test_seed_must_match_the_simulator_seed_before_wiping(self):
        # K7 / QA Y3: make scenario-reset SEED=n also sets the simulator seed.
        project_import_v1()
        with self.assertRaisesMessage(CommandError, "SIMULATOR_SEED is 7, expected 1"):
            call_command("scenario_reset", "import_v1", "--noinput", "--seed", "1", stdout=io.StringIO())
        self.assertTrue(SiteProjection.objects.exists())

        out = io.StringIO()
        call_command("scenario_reset", "import_v1", "--noinput", "--seed", "7", stdout=out)
        self.assertFalse(SiteProjection.objects.exists())
        self.assertIn("simulator seed 7", out.getvalue())

    @override_settings(SIMULATOR_SIM_SETTINGS={})
    def test_seed_check_fails_when_the_simulator_seed_is_unset(self):
        with self.assertRaisesMessage(CommandError, "SIMULATOR_SEED is unset, expected 1"):
            call_command("scenario_reset", "import_v1", "--noinput", "--seed", "1", stdout=io.StringIO())

    def test_interactive_reset_needs_confirmation(self):
        project_import_v1()
        with mock.patch("builtins.input", return_value="no"):
            with self.assertRaises(CommandError):
                call_command("scenario_reset", "import_v1", stdout=io.StringIO())
        self.assertTrue(SiteProjection.objects.exists())

    def test_dump_command_prints_checksum(self):
        project_import_v1()
        out = io.StringIO()
        call_command("scenario_dump", "--checksum", stdout=out)
        self.assertEqual(out.getvalue().strip(), canonical_checksum(canonical_dump()))


class ScenarioWaitReadyTests(TestCase):
    def test_returns_ready_site(self):
        project_import_v1()
        site = wait_for_ready_site("test-terminal", timeout=0, sleep=lambda _: None)
        self.assertEqual(site.source_id, IMPORT_V1_SITE_ID)

    def test_times_out_with_a_hint(self):
        ticks = iter([0.0, 0.5, 2.0])
        with self.assertRaisesMessage(TimeoutError, "OES consumer"):
            wait_for_ready_site(
                "test-terminal", timeout=1, sleep=lambda _: None, clock=lambda: next(ticks),
            )
        with self.assertRaises(CommandError):
            call_command("scenario_wait_ready", "--timeout", "0", stdout=io.StringIO())


class ScenarioDispatchStepTests(TestCase):
    def setUp(self):
        self.site, events = project_import_v1()
        released = events["scms.order.released.v1"]
        self.order = ingest_released_order(released["topic"], released["payload"])
        self.session = SimulationSession.objects.create(
            site=self.site, configuration_revision=1, configuration_checksum="a" * 64,
            sparkplug_group_id="TERMINAL_VELOCITY", sparkplug_node_id="SCMS_TV_TEST_TERMINAL",
            actual_state=SimulationSession.RUNNING, node_connected=True,
            seed=20261026, start_epoch_ms=1_792_886_400_000,
        )
        # What Godot's DBIRTHs would do: every seeded CHE comes online.
        for equipment in EquipmentState.objects.filter(site=self.site):
            equipment.simulation_session = self.session
            equipment.sparkplug_device_id = equipment.name
            equipment.powered_on = True
            equipment.is_available = True
            equipment.device_connected = True
            equipment.data_valid = True
            equipment.last_seen_at = timezone.now()
            equipment.save()

    def test_released_import_v1_order_has_the_handoff_shape(self):
        operations = list(self.order.operations.order_by("sequence"))
        self.assertEqual(len(operations), 3)
        self.assertEqual(
            [operation.destination_inventory_position_id is None for operation in operations],
            [True, True, False],
        )
        self.assertEqual(operations[1].required_movement_mode, "road")

    def test_dispatch_next_pairs_qc_and_tt(self):
        out = io.StringIO()
        call_command("scenario_step", "import_v1", "dispatch-next", stdout=out)

        assigned = sorted(
            DispatchAssignment.objects.filter(operation__order=self.order)
            .values_list("operation__sequence", "equipment__name")
        )
        self.assertEqual(assigned[0], (1, "QC01"))
        self.assertEqual(assigned[1][0], 2)
        self.assertIn(assigned[1][1], {"TT01", "TT02"})
        self.assertIn(f"TOS order {self.order.tos_order_id}", out.getvalue())

    def test_dispatch_next_without_released_orders_fails_clearly(self):
        ExecutionOrder.objects.all().delete()
        with self.assertRaisesMessage(CommandError, "release-discharge"):
            call_command("scenario_step", "import_v1", "dispatch-next", stdout=io.StringIO())
