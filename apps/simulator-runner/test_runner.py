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
    ALLOWED_ENVIRONMENT, DockerLauncher, OESClient, ProcessLauncher, Runner, SessionStore, SessionWorker,
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
        self.assertEqual(self.store.load_all(), [claim])
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
        self.assertEqual(saved_at_start, [[claim]])


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
        self.assertLessEqual(len(self.client.report.call_args.args[2]), 1000)

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
