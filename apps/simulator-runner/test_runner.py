import unittest
import uuid
from unittest.mock import Mock, patch

from runner import ALLOWED_ENVIRONMENT, DockerLauncher, Runner, SessionWorker


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
            "SIMULATOR_COMMAND_TTL_S": "600",
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


if __name__ == "__main__":
    unittest.main()
