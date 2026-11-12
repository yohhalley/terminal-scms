import io
import json
import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
import uuid
from unittest.mock import Mock, patch

from runner import (
    ALLOWED_ENVIRONMENT, FAILURE_REASON_LIMIT, TRUNCATION_MARKER, DockerLauncher, OESClient,
    ProcessLauncher, Runner, SHUTDOWN_STOP_REASON, SessionStore, SessionWorker,
    bound_failure_reason,
)


def launch_environment():
    values = {key: f"value-{key.lower()}" for key in ALLOWED_ENVIRONMENT}
    values["SIMULATOR_SESSION_ID"] = str(uuid.uuid4())
    values["SIMULATOR_SITE_ID"] = str(uuid.uuid4())
    values["SIMULATOR_CONFIG_REVISION"] = "3"
    values["MQTT_PORT"] = "1883"
    return values


class DockerLauncherTests(unittest.TestCase):
    def test_launch_command_is_fixed_to_allowlisted_image_and_environment(self):
        launcher = DockerLauncher("terminal-scms/godot-simulator:local")
        name, command = launcher.command(launch_environment())

        self.assertTrue(name.startswith("terminal-scms-sim-"))
        self.assertEqual(command[-1], "terminal-scms/godot-simulator:local")
        self.assertNotIn("--privileged", command)
        self.assertNotIn("/var/run/docker.sock", " ".join(command))
        self.assertEqual(command.count("--env"), len(ALLOWED_ENVIRONMENT))

    def test_simulation_settings_are_passed_to_the_container(self):
        settings = {
            "SIMULATOR_SEED": "42",
            "SIMULATOR_RUN_ID": "run-42",
            "SIMULATOR_TIME_SCALE": "4.0",
            "SIMULATOR_FIXED_STEP_MS": "100",
            "SIMULATOR_START_EPOCH_MS": "1764583200000",
            "SIMULATOR_DEVICE_ID_MODE": "name",
            "SIMULATOR_QC_WAIT_MIN_S": "10",
            "SIMULATOR_QC_WAIT_MAX_S": "30",
            "SIMULATOR_QC_HOLD_WAIT_MIN_S": "12",
            "SIMULATOR_QC_HOLD_WAIT_MAX_S": "28",
            "SIMULATOR_RTG_WAIT_MIN_S": "10",
            "SIMULATOR_RTG_WAIT_MAX_S": "30",
            "SIMULATOR_RTG_HOLD_WAIT_MIN_S": "15",
            "SIMULATOR_RTG_HOLD_WAIT_MAX_S": "25",
            "SIMULATOR_TT_NO_SHOW_TIMEOUT_S": "300",
            "SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED": "false",
            "SIMULATOR_COMMAND_TTL_S": "600",
            "SIMULATOR_SCENARIO_ID": "import-discharge-ci",
            "SIMULATOR_DEFAULT_SPEED_MPS": "4.0",
        }
        environment = {
            key: value for key, value in launch_environment().items()
            if not key.startswith("SIMULATOR_") or key in {
                "SIMULATOR_SITE_ID", "SIMULATOR_SESSION_ID", "SIMULATOR_TOKEN",
                "SIMULATOR_CONFIG_REVISION", "SIMULATOR_CONFIG_CHECKSUM",
            }
        }
        environment.update(settings)
        _, command = DockerLauncher("allowed").command(environment)
        for key, value in settings.items():
            self.assertIn(f"{key}={value}", command)

    def test_simulation_settings_are_optional(self):
        environment = {
            key: value for key, value in launch_environment().items()
            if key in {
                "OES_URL", "SIMULATOR_SITE_ID", "SIMULATOR_SESSION_ID", "SIMULATOR_TOKEN",
                "SIMULATOR_CONFIG_REVISION", "SIMULATOR_CONFIG_CHECKSUM",
                "MQTT_HOST", "MQTT_PORT", "SPARKPLUG_GROUP_ID", "SPARKPLUG_NODE_ID",
            }
        }
        _, command = DockerLauncher("allowed").command(environment)
        self.assertFalse(any(part.startswith("SIMULATOR_SEED=") for part in command))

    def test_local_sim_aliases_are_not_passed_through(self):
        environment = launch_environment()
        environment["SIM_SEED"] = "42"
        with self.assertRaisesRegex(ValueError, "SIM_SEED"):
            DockerLauncher("allowed").command(environment)

    def test_unknown_environment_is_rejected(self):
        environment = launch_environment()
        environment["UNTRUSTED_DOCKER_ARGUMENT"] = "--privileged"
        with self.assertRaisesRegex(ValueError, "unknown"):
            DockerLauncher("allowed").command(environment)


class RunnerTests(unittest.TestCase):
    def test_no_claim_does_not_start_process(self):
        client = Mock()
        client.claim.return_value = None
        launcher = Mock()
        runner = Runner(client, launcher)

        self.assertFalse(runner.run_once())
        launcher.start.assert_not_called()

    @patch.object(SessionWorker, "start")
    def test_claim_starts_exactly_one_worker(self, start):
        session_id = uuid.uuid4()
        client = Mock()
        client.claim.return_value = {
            "session": {"id": str(session_id)},
            "environment": launch_environment(),
        }
        launcher = Mock()
        runner = Runner(client, launcher)

        self.assertTrue(runner.run_once())
        self.assertEqual(set(runner.workers), {str(session_id)})
        start.assert_called_once()


def claim_for(environment=None):
    environment = environment or launch_environment()
    return {"session": {"id": environment["SIMULATOR_SESSION_ID"]}, "environment": environment}


class ProcessLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_root = os.path.join(self.tmp.name, "sessions")

    def test_child_gets_only_the_allowlist_and_a_per_session_home(self):
        environment = launch_environment()
        launcher = ProcessLauncher("godot", "/app", self.data_root)
        with patch.dict(os.environ, {"OES_API_TOKEN": "business-secret", "PATH": "/usr/bin"}):
            name, command, child = launcher.command(environment)

        session_id = environment["SIMULATOR_SESSION_ID"]
        self.assertTrue(name.startswith("terminal-scms-sim-"))
        self.assertEqual(command, [
            "godot", "--headless", "--path", "/app", "res://source/headless/HeadlessSimulator.tscn",
        ])
        extra = {"PATH", "HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}
        self.assertEqual(set(child) - extra, set(environment))
        self.assertNotIn("OES_API_TOKEN", child)
        self.assertNotIn("SIMULATOR_RUNNER_TOKEN", child)
        self.assertEqual(child["HOME"], os.path.join(self.data_root, session_id))
        self.assertTrue(child["XDG_DATA_HOME"].startswith(child["HOME"]))

    def test_unknown_or_missing_environment_is_rejected(self):
        launcher = ProcessLauncher("godot", "/app", self.data_root)
        environment = launch_environment()
        environment["LD_PRELOAD"] = "/tmp/evil.so"
        with self.assertRaisesRegex(ValueError, "LD_PRELOAD"):
            launcher.command(environment)
        environment = launch_environment()
        del environment["SIMULATOR_TOKEN"]
        with self.assertRaisesRegex(ValueError, "SIMULATOR_TOKEN"):
            launcher.command(environment)

    def test_start_runs_the_binary_and_stop_terminates_it(self):
        out = os.path.join(self.tmp.name, "env.json")
        fake = os.path.join(self.tmp.name, "fake-godot")
        with open(fake, "w", encoding="utf-8") as handle:
            handle.write(textwrap.dedent(f"""\
                #!{sys.executable}
                import json, os, sys, time
                json.dump({{"argv": sys.argv[1:], "env": dict(os.environ)}}, open({out!r}, "w"))
                time.sleep(60)
            """))
        os.chmod(fake, 0o755)
        launcher = ProcessLauncher(fake, "/app", self.data_root, stop_timeout=5)
        environment = launch_environment()
        name, process = launcher.start(environment)
        try:
            for _ in range(100):
                if os.path.exists(out) and os.path.getsize(out):
                    break
                time.sleep(0.05)
            seen = json.load(open(out, encoding="utf-8"))
        finally:
            launcher.stop(name)
        self.assertIsNotNone(process.poll())
        self.assertEqual(seen["argv"][0], "--headless")
        self.assertEqual(seen["env"]["SIMULATOR_SESSION_ID"], environment["SIMULATOR_SESSION_ID"])
        self.assertTrue(os.path.isdir(seen["env"]["XDG_DATA_HOME"]))
        launcher.forget(environment["SIMULATOR_SESSION_ID"])
        self.assertFalse(os.path.exists(launcher.session_home(environment["SIMULATOR_SESSION_ID"])))


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(os.path.join(self.tmp.name, "runner"))

    def test_save_is_private_and_round_trips(self):
        claim = claim_for()
        self.store.save(claim)
        path = self.store._path(claim["session"]["id"])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(
            self.store.load_all(), [dict(claim, state="running", failure_reason="")],
        )
        self.store.mark(claim, "stopped")
        self.assertEqual(self.store.load_all()[0]["state"], "stopped")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with self.assertRaises(ValueError):
            self.store.mark(claim, "running")
        self.store.remove(claim["session"]["id"])
        self.assertEqual(self.store.load_all(), [])
        self.store.remove(claim["session"]["id"])  # idempotent

    def test_unreadable_state_is_discarded(self):
        os.makedirs(self.store.directory)
        with open(os.path.join(self.store.directory, "broken.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(self.store.load_all(), [])
        self.assertEqual(os.listdir(self.store.directory), [])


class RestartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(os.path.join(self.tmp.name, "runner"))
        self.claim = claim_for()
        self.session_id = self.claim["session"]["id"]
        self.store.save(self.claim)
        self.client = Mock()
        self.client.claim.return_value = None
        self.launcher = Mock()

    @patch.object(SessionWorker, "start")
    def test_a_saved_session_oes_still_serves_is_resumed(self, start):
        self.client.bootstrap_status.return_value = "ok"
        runner = Runner(self.client, self.launcher, store=self.store)

        self.assertFalse(runner.run_once())
        self.client.bootstrap_status.assert_called_once_with(
            self.session_id, self.claim["environment"]["SIMULATOR_TOKEN"],
        )
        self.assertEqual(set(runner.workers), {self.session_id})
        start.assert_called_once()
        self.client.report.assert_not_called()
        self.assertEqual(len(self.store.load_all()), 1)

    def test_an_expired_session_is_released_as_failed(self):
        self.client.bootstrap_status.return_value = "gone"
        runner = Runner(self.client, self.launcher, store=self.store)

        runner.run_once()
        status, reason = self.client.report.call_args.args[1:]
        self.assertEqual(status, "failed")
        self.assertIn("restarted", reason)
        self.assertEqual(self.store.load_all(), [])
        self.launcher.forget.assert_called_once_with(self.session_id)
        self.assertEqual(runner.workers, {})

    def test_a_session_oes_is_stopping_is_released_as_stopped(self):
        self.client.bootstrap_status.return_value = "stopping"
        Runner(self.client, self.launcher, store=self.store).run_once()
        self.assertEqual(self.client.report.call_args.args[1], "stopped")
        self.assertEqual(self.store.load_all(), [])

    def test_a_session_wiped_by_a_reset_is_dropped(self):
        self.client.bootstrap_status.return_value = "gone"
        self.client.report.side_effect = RuntimeError("OES HTTP 404: not found")
        runner = Runner(self.client, self.launcher, store=self.store)
        runner.run_once()
        self.assertEqual(self.store.load_all(), [])
        self.assertTrue(runner.recovered)

    def test_nothing_is_claimed_until_recovery_has_reached_oes(self):
        self.client.bootstrap_status.side_effect = urllib.error.URLError("connection refused")
        runner = Runner(self.client, self.launcher, store=self.store)
        with self.assertRaises(urllib.error.URLError):
            runner.run_once()
        self.client.claim.assert_not_called()
        self.assertFalse(runner.recovered)
        self.assertEqual(len(self.store.load_all()), 1)

    @patch.object(SessionWorker, "start")
    def test_a_claim_is_saved_before_godot_starts(self, start):
        self.store.remove(self.session_id)
        claim = claim_for()
        self.client.claim.return_value = claim
        saved_at_start = []
        start.side_effect = lambda: saved_at_start.append(self.store.load_all())
        Runner(self.client, self.launcher, store=self.store).run_once()
        self.assertEqual(saved_at_start, [[dict(claim, state="running", failure_reason="")]])


class FakeOES:
    """OES as seen by the runner, with a clock and the 300 s bootstrap token TTL."""

    TOKEN_TTL_S = 300

    def __init__(self):
        self.now = 0.0
        self.issued_at = 0.0
        self.desired_state = "running"
        self.actual_state = "running"
        self.reports = []
        self.reasons = []
        self.bootstrap_checks = 0
        self.reachable = True

    def claim(self):
        return None

    def bootstrap_status(self, session_id, token):
        self.bootstrap_checks += 1
        if self.now - self.issued_at >= self.TOKEN_TTL_S:
            return "gone"
        return "ok" if self.desired_state == "running" else "stopping"

    def report(self, session_id, status, failure_reason=""):
        if not self.reachable:
            raise urllib.error.URLError("connection refused")
        self.reports.append(status)
        self.reasons.append(failure_reason)
        if status == "stopped":
            self.actual_state = self.desired_state = "stopped"
        elif status == "failed":
            self.actual_state = "failed"
        return {"desired_state": self.desired_state}


class StoppedStateAcrossRestartTests(unittest.TestCase):
    """A cleanly stopped session stays stopped after a runner restart (TCL-033)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(os.path.join(self.tmp.name, "runner"))
        self.claim = claim_for()
        self.store.save(self.claim)
        self.oes = FakeOES()
        self.launcher = Mock()
        self.process = Mock()
        self.process.poll.return_value = None
        self.launcher.start.return_value = ("sim", self.process)

    def stop_cleanly_but_die_before_oes_hears_it(self):
        """The runner stops Godot, then OES is unreachable and the runner is killed."""
        worker = SessionWorker(self.oes, self.launcher, self.claim, 0.01, store=self.store)
        worker.FINAL_RETRY_DELAYS = ()
        worker.container_name, worker.process = "sim", self.process
        worker.request_stop()                 # SIGTERM / operator stop
        self.oes.reachable = False            # ... OES unreachable for the final report
        status, reason = "stopped", ""
        worker._mark(status, reason)
        worker._report_final(status, reason)
        self.assertFalse(worker.final_reported)
        self.assertEqual(self.store.load_all()[0]["state"], "stopped")
        self.oes.reachable = True             # runner process dies here; OES comes back

    def restart(self, after_s):
        self.oes.now = after_s
        runner = Runner(self.oes, self.launcher, store=self.store)
        with patch.object(SessionWorker, "start") as start:
            runner.run_once()
        return runner, start

    def test_stopped_then_restart_before_the_timeout_stays_stopped(self):
        self.stop_cleanly_but_die_before_oes_hears_it()
        runner, start = self.restart(after_s=10)
        self.assertEqual(self.oes.reports, ["stopped"])
        self.assertEqual(self.oes.actual_state, "stopped")
        self.assertEqual(self.oes.bootstrap_checks, 0)
        start.assert_not_called()
        self.assertEqual(self.store.load_all(), [])

    def test_stopped_then_restart_after_the_timeout_stays_stopped(self):
        self.stop_cleanly_but_die_before_oes_hears_it()
        runner, start = self.restart(after_s=FakeOES.TOKEN_TTL_S + 100)
        self.assertEqual(self.oes.reports, ["stopped"])
        self.assertNotIn("failed", self.oes.reports)
        self.assertEqual(self.oes.actual_state, "stopped")
        start.assert_not_called()
        self.assertEqual(self.store.load_all(), [])

    def test_stop_is_recorded_before_godot_is_touched(self):
        worker = SessionWorker(self.oes, self.launcher, self.claim, 0.01, store=self.store)
        worker.container_name = "sim"
        seen = []
        self.launcher.stop.side_effect = lambda name: seen.append(self.store.load_all()[0]["state"])
        worker.request_stop()
        self.assertEqual(seen, ["stopped"])

    def test_a_reported_stop_leaves_nothing_to_redo_after_a_restart(self):
        self.oes.desired_state = "stopped"     # operator pressed Stop in OES
        worker = SessionWorker(self.oes, self.launcher, self.claim, 0.01, store=self.store)
        worker.run()
        self.assertEqual(self.oes.reports[-1], "stopped")
        self.assertEqual(self.store.load_all(), [])
        self.oes.reports.clear()
        self.restart(after_s=FakeOES.TOKEN_TTL_S + 100)
        self.assertEqual(self.oes.reports, [])
        self.assertEqual(self.oes.actual_state, "stopped")

    def test_an_error_while_stopping_is_still_stopped(self):
        self.launcher.stop.side_effect = OSError("kill failed")
        self.oes.desired_state = "stopped"
        SessionWorker(self.oes, self.launcher, self.claim, 0.01, store=self.store).run()
        self.assertEqual(self.oes.reports[-1], "stopped")
        self.assertNotIn("failed", self.oes.reports)

    def test_a_runner_shutdown_reports_stopped_with_its_reason(self):
        # SIGTERM/SIGINT set Runner.stopping; serve_forever then stops every
        # worker as a runner shutdown (TL server trial, PR #29).
        worker = SessionWorker(self.oes, self.launcher, self.claim, 0.01, store=self.store)
        runner = Runner(self.oes, self.launcher, store=self.store)
        runner.recovered = True
        runner.workers[worker.session_id] = worker
        worker.start()
        runner.stopping = True
        runner.serve_forever()
        self.assertFalse(worker.is_alive())
        self.launcher.stop.assert_called_with("sim")
        self.assertEqual(self.oes.reports[-1], "stopped")
        self.assertEqual(self.oes.reasons[-1], SHUTDOWN_STOP_REASON)
        self.assertEqual(SHUTDOWN_STOP_REASON, "stopped: runner shutdown")
        self.assertEqual(self.store.load_all(), [])

    def test_a_shutdown_reason_survives_a_restart_before_oes_hears_it(self):
        worker = SessionWorker(self.oes, self.launcher, self.claim, 0.01, store=self.store)
        worker.container_name, worker.process = "sim", self.process
        worker.request_stop(SHUTDOWN_STOP_REASON)
        self.assertEqual(self.store.load_all()[0]["failure_reason"], SHUTDOWN_STOP_REASON)
        self.restart(after_s=10)               # runner died before reporting
        self.assertEqual(self.oes.reports, ["stopped"])
        self.assertEqual(self.oes.reasons, [SHUTDOWN_STOP_REASON])

    def test_an_oes_requested_stop_has_no_shutdown_reason(self):
        self.oes.desired_state = "stopped"
        SessionWorker(self.oes, self.launcher, self.claim, 0.01, store=self.store).run()
        self.assertEqual(self.oes.reports[-1], "stopped")
        self.assertEqual(self.oes.reasons[-1], "")

    def test_crash_then_restart_before_the_timeout_resumes(self):
        # Runner and Godot killed (kill -9): the saved state is still "running".
        runner, start = self.restart(after_s=10)
        start.assert_called_once()
        self.assertEqual(set(runner.workers), {self.claim["session"]["id"]})
        self.assertEqual(self.oes.reports, [])
        self.assertEqual(self.store.load_all()[0]["state"], "running")

    def test_crash_then_restart_after_the_timeout_fails(self):
        runner, start = self.restart(after_s=FakeOES.TOKEN_TTL_S + 100)
        start.assert_not_called()
        self.assertEqual(self.oes.reports, ["failed"])
        self.assertEqual(self.oes.actual_state, "failed")
        self.assertEqual(self.store.load_all(), [])

    def test_a_recorded_failure_is_reported_as_failed_with_its_reason(self):
        self.store.mark(self.claim, "failed", "Godot container exited with code 3.")
        reasons = []
        self.oes.report = lambda sid, status, reason="": reasons.append((status, reason))
        self.restart(after_s=10)
        self.assertEqual(reasons, [("failed", "Godot container exited with code 3.")])


class OESClientErrorTests(unittest.TestCase):
    def test_http_error_detail_is_collapsed_and_truncated(self):
        page = "<html>\n" + ("META SECRET_LOOKING_VALUE " * 500) + "</html>"
        error = urllib.error.HTTPError("http://oes/x", 500, "boom", {}, io.BytesIO(page.encode()))
        client = OESClient("http://oes", "runner-token", "test-runner")
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                client.report(str(uuid.uuid4()), "running")
        message = str(raised.exception)
        self.assertTrue(message.startswith("OES HTTP 500: <html> META"))
        self.assertLessEqual(len(message), 320)
        self.assertNotIn("\n", message)


class SessionWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SessionStore(os.path.join(self.tmp.name, "runner"))
        self.claim = claim_for()
        self.store.save(self.claim)
        self.process = Mock()
        self.launcher = Mock()
        self.launcher.start.return_value = ("sim", self.process)
        self.client = Mock()

    def worker(self):
        worker = SessionWorker(self.client, self.launcher, self.claim, 0.01, store=self.store)
        worker.FINAL_RETRY_DELAYS = (0, 0)
        return worker

    def test_clean_exit_reports_stopped_and_forgets_the_claim(self):
        self.process.poll.return_value = 0
        self.worker().run()
        self.assertEqual(self.client.report.call_args.args[1], "stopped")
        self.assertEqual(self.store.load_all(), [])

    def test_a_crash_reports_failed(self):
        self.process.poll.return_value = 3
        self.worker().run()
        self.assertEqual(self.client.report.call_args.args[1], "failed")
        self.assertIn("code 3", self.client.report.call_args.args[2])

    def test_claim_is_kept_when_oes_never_heard_the_outcome(self):
        self.process.poll.return_value = 0
        self.client.report.side_effect = [None] + [RuntimeError("down")] * 3
        self.worker().run()
        self.assertEqual(len(self.store.load_all()), 1)
        self.assertEqual(self.client.report.call_count, 4)

    def test_the_final_status_is_retried_and_a_report_error_is_not_a_failure(self):
        # Seen locally: OES answered 500 to the final "stopped"; the runner must
        # retry "stopped", not turn the reporting error into a "failed" session.
        self.process.poll.return_value = 0
        self.client.report.side_effect = [None, RuntimeError("OES HTTP 500"), None]
        self.worker().run()
        statuses = [call.args[1] for call in self.client.report.call_args_list]
        self.assertEqual(statuses, ["starting", "stopped", "stopped"])
        self.assertEqual(self.store.load_all(), [])

    def test_failure_reason_is_bounded(self):
        self.launcher.start.side_effect = RuntimeError("x" * 5000)
        self.worker().run()
        self.assertEqual(self.client.report.call_args.args[1], "failed")
        self.assertLessEqual(len(self.client.report.call_args.args[2]), FAILURE_REASON_LIMIT)

    def test_a_long_crash_traceback_is_capped_with_a_marker_and_logged_in_full(self):
        # OES answers HTTP 400 to a failure_reason over 2000 characters.
        self.assertLessEqual(FAILURE_REASON_LIMIT, 1900)

        def crash(depth):
            if depth == 0:
                raise RuntimeError("Godot launch failed: " + "detail " * 800 + "END-OF-MESSAGE")
            crash(depth - 1)

        self.launcher.start.side_effect = lambda environment: crash(60)
        sent = []

        def urlopen(request, timeout=None):
            sent.append(json.loads(request.data.decode("utf-8")))
            response = Mock(status=204)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            return response

        self.client = OESClient("http://oes", "runner-token", "test-runner")
        with patch("urllib.request.urlopen", side_effect=urlopen):
            with self.assertLogs("terminal-simulator-runner", level="ERROR") as logs:
                self.worker().run()
        final = sent[-1]
        self.assertEqual(final["status"], "failed")
        self.assertLessEqual(len(final["failure_reason"]), FAILURE_REASON_LIMIT)
        self.assertTrue(final["failure_reason"].endswith(TRUNCATION_MARKER))
        self.assertTrue(final["failure_reason"].startswith("Traceback (most recent call last):"))
        logged = "\n".join(logs.output)
        trace = [line for line in logs.output if "Traceback (most recent call last):" in line]
        self.assertEqual(len(trace), 1)
        self.assertGreater(len(trace[0]), 5000)
        self.assertIn("END-OF-MESSAGE", logged)
        self.assertIn("in crash", trace[0])
        # Reported: nothing is left for a restart to report again.
        self.assertEqual(self.store.load_all(), [])

    def test_bound_failure_reason(self):
        self.assertEqual(bound_failure_reason("short"), "short")
        self.assertEqual(bound_failure_reason(None), "")
        exact = "y" * FAILURE_REASON_LIMIT
        self.assertEqual(bound_failure_reason(exact), exact)
        cut = bound_failure_reason("y" * (FAILURE_REASON_LIMIT + 1))
        self.assertEqual(len(cut), FAILURE_REASON_LIMIT)
        self.assertTrue(cut.endswith(TRUNCATION_MARKER))

    def test_every_report_path_bounds_the_reason(self):
        sent = []

        def urlopen(request, timeout=None):
            sent.append(json.loads(request.data.decode("utf-8")))
            response = Mock(status=204)
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            return response

        client = OESClient("http://oes", "runner-token", "test-runner")
        with patch("urllib.request.urlopen", side_effect=urlopen):
            client.report(str(uuid.uuid4()), "failed", "z" * 6000)
        self.assertLessEqual(len(sent[0]["failure_reason"]), FAILURE_REASON_LIMIT)
        self.assertIn(TRUNCATION_MARKER, sent[0]["failure_reason"])

    def test_a_short_oes_outage_does_not_stop_the_simulation(self):
        self.process.poll.side_effect = [None, None, 0]
        self.client.report.side_effect = [
            None, RuntimeError("OES HTTP 502"), {"desired_state": "running"}, None,
        ]
        self.worker().run()
        self.launcher.stop.assert_not_called()
        self.assertEqual(self.client.report.call_args.args[1], "stopped")

    def test_oes_stop_request_stops_godot(self):
        self.process.poll.return_value = None
        self.client.report.side_effect = [None, {"desired_state": "stopped"}, None]
        self.worker().run()
        self.launcher.stop.assert_called_with("sim")
        self.assertEqual(self.client.report.call_args.args[1], "stopped")
        self.assertEqual(self.store.load_all(), [])


if __name__ == "__main__":
    unittest.main()
