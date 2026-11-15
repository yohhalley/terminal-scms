"""Runner (S2): the simulation_start / simulation_stop management commands."""

import io
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import SimulationSession
from .services.simulations import claim_next_session, report_runner_status
from .test_scenario_reset import project_import_v1


def run(*args):
    out = io.StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


class SimulationStartCommandTests(TestCase):
    def test_starts_one_pending_session_for_the_ready_site_and_is_idempotent(self):
        site, _ = project_import_v1()

        first = run("simulation_start")
        second = run("simulation_start")

        session = SimulationSession.objects.get()
        self.assertEqual(session.site, site)
        self.assertEqual(session.actual_state, SimulationSession.PENDING)
        self.assertEqual(session.desired_state, SimulationSession.RUNNING_DESIRED)
        self.assertIn("created", first)
        self.assertIn("already active", second)

    def test_time_scale_is_stored_on_the_session(self):
        project_import_v1()
        run("simulation_start", "--time-scale", "4")
        self.assertEqual(SimulationSession.objects.get().time_scale, 4.0)

    def test_time_scale_outside_the_model_range_is_refused(self):
        project_import_v1()
        for value in ("0", "-1", "0.001", "100.5", "nan"):
            with self.subTest(time_scale=value):
                with self.assertRaisesMessage(CommandError, "--time-scale must be between 0.01 and 100"):
                    run("simulation_start", "--time-scale", value)
        self.assertFalse(SimulationSession.objects.exists())

    def test_time_scale_range_bounds_are_accepted(self):
        project_import_v1()
        run("simulation_start", "--time-scale", "0.01")
        self.assertEqual(SimulationSession.objects.get().time_scale, 0.01)
        run("simulation_stop", "--wait", "0")
        run("simulation_start", "--time-scale", "100")
        self.assertEqual(
            SimulationSession.objects.exclude(actual_state=SimulationSession.STOPPED).get().time_scale,
            100.0,
        )

    def test_start_while_the_previous_session_is_stopping_fails(self):
        project_import_v1()
        run("simulation_start")
        claim_next_session("runner-1")
        with self.assertRaises(CommandError):
            run("simulation_stop", "--wait", "0")
        session = SimulationSession.objects.get()
        self.assertEqual(session.actual_state, SimulationSession.STOPPING)

        with self.assertRaisesMessage(CommandError, "still stopping"):
            run("simulation_start")
        self.assertEqual(SimulationSession.objects.count(), 1)

    def test_unknown_site_is_a_clear_error(self):
        with self.assertRaisesMessage(CommandError, "scenario-reset"):
            run("simulation_start", "--site", "nowhere")


class SimulationStopCommandTests(TestCase):
    def test_nothing_to_stop(self):
        self.assertIn("No active", run("simulation_stop"))

    def test_an_unclaimed_session_stops_at_once(self):
        project_import_v1()
        run("simulation_start")
        self.assertIn("Stopped 1", run("simulation_stop", "--wait", "0"))
        self.assertEqual(SimulationSession.objects.get().actual_state, SimulationSession.STOPPED)

    def test_waits_for_the_runner_to_confirm_a_running_session(self):
        project_import_v1()
        run("simulation_start")
        session, _token = claim_next_session("runner-1")
        report_runner_status(session.pk, "runner-1", SimulationSession.RUNNER_RUNNING)

        def runner_confirms(_seconds):
            report_runner_status(session.pk, "runner-1", SimulationSession.RUNNER_STOPPED)

        with mock.patch("execution.management.commands.simulation_stop.time.sleep",
                        side_effect=runner_confirms):
            output = run("simulation_stop", "--wait", "5", "--interval", "0")
        self.assertIn("Stopped 1", output)
        session.refresh_from_db()
        self.assertEqual(session.actual_state, SimulationSession.STOPPED)

    def test_fails_clearly_when_no_runner_confirms(self):
        project_import_v1()
        run("simulation_start")
        claim_next_session("runner-1")
        with self.assertRaisesMessage(CommandError, "simulator-runner"):
            run("simulation_stop", "--wait", "0")
        self.assertEqual(
            SimulationSession.objects.get().actual_state, SimulationSession.STOPPING,
        )
