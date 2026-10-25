#!/usr/bin/env python3
import json
import logging
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid


LOG = logging.getLogger("terminal-simulator-runner")
ALLOWED_ENVIRONMENT = {
    "OES_URL",
    "SIMULATOR_SITE_ID",
    "SIMULATOR_SESSION_ID",
    "SIMULATOR_TOKEN",
    "SIMULATOR_CONFIG_REVISION",
    "SIMULATOR_CONFIG_CHECKSUM",
    "SIMULATOR_VERSION",
    "SIMULATOR_TIME_SCALE",
    "SIMULATOR_DEFAULT_SPEED_MPS",
    # Deterministic simulation settings read by the headless Godot simulator
    # (TCL-014a). Explicit names keep the allowlist closed; SIM_* aliases are
    # for local runs only and are not passed through.
    "SIMULATOR_SEED",
    "SIMULATOR_RUN_ID",
    "SIMULATOR_FIXED_STEP_MS",
    "SIMULATOR_START_EPOCH_MS",
    "SIMULATOR_DEVICE_ID_MODE",
    "SIMULATOR_QC_WAIT_MIN_S",
    "SIMULATOR_QC_WAIT_MAX_S",
    "SIMULATOR_QC_HOLD_WAIT_MIN_S",
    "SIMULATOR_QC_HOLD_WAIT_MAX_S",
    "SIMULATOR_RTG_WAIT_MIN_S",
    "SIMULATOR_RTG_WAIT_MAX_S",
    "SIMULATOR_RTG_HOLD_WAIT_MIN_S",
    "SIMULATOR_RTG_HOLD_WAIT_MAX_S",
    "SIMULATOR_TT_NO_SHOW_TIMEOUT_S",
    "SIMULATOR_COMMAND_TTL_S",
    "MQTT_HOST",
    "MQTT_PORT",
    "MQTT_USERNAME",
    "MQTT_PASSWORD",
    "MQTT_CLIENT_ID",
    "SPARKPLUG_GROUP_ID",
    "SPARKPLUG_NODE_ID",
    "SPARKPLUG_PRIMARY_HOST_ID",
}
REQUIRED_ENVIRONMENT = {
    "OES_URL",
    "SIMULATOR_SITE_ID",
    "SIMULATOR_SESSION_ID",
    "SIMULATOR_TOKEN",
    "SIMULATOR_CONFIG_REVISION",
    "SIMULATOR_CONFIG_CHECKSUM",
    "MQTT_HOST",
    "MQTT_PORT",
    "SPARKPLUG_GROUP_ID",
    "SPARKPLUG_NODE_ID",
}


class OESClient:
    def __init__(self, base_url, runner_token, runner_identity, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.runner_token = runner_token
        self.runner_identity = runner_identity
        self.timeout = timeout

    def _request(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Runner-Token": self.runner_token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status == 204:
                    return None
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OES HTTP {exc.code}: {detail}") from exc

    def claim(self):
        return self._request("POST", "/api/simulation-sessions/claim/", {
            "runner_identity": self.runner_identity,
        })

    def report(self, session_id, status, failure_reason=""):
        return self._request(
            "POST",
            f"/api/simulation-sessions/{session_id}/runner-status/",
            {
                "runner_identity": self.runner_identity,
                "status": status,
                "failure_reason": failure_reason,
            },
        )


class DockerLauncher:
    def __init__(self, image, network="terminal-scms", docker_binary="docker"):
        self.image = image
        self.network = network
        self.docker_binary = docker_binary

    @staticmethod
    def _identity(environment):
        session_id = str(uuid.UUID(environment["SIMULATOR_SESSION_ID"]))
        site_id = str(uuid.UUID(environment["SIMULATOR_SITE_ID"]))
        return session_id, site_id

    def command(self, environment):
        unknown = set(environment) - ALLOWED_ENVIRONMENT
        missing = REQUIRED_ENVIRONMENT - set(environment)
        if unknown or missing:
            raise ValueError(
                f"Invalid launch environment; missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        session_id, site_id = self._identity(environment)
        name = f"terminal-scms-sim-{session_id.replace('-', '')[:16]}"
        volume = f"terminal-scms-sim-{session_id.replace('-', '')}"
        command = [
            self.docker_binary, "run", "--rm", "--name", name,
            "--network", self.network,
            "--label", f"scms.simulation-session={session_id}",
            "--label", f"scms.site={site_id}",
            "--mount", f"type=volume,source={volume},target=/data",
        ]
        for key in sorted(ALLOWED_ENVIRONMENT):
            if key in environment:
                command.extend(["--env", f"{key}={environment[key]}"])
        command.append(self.image)
        return name, command

    def start(self, environment):
        name, command = self.command(environment)
        LOG.info("Starting allowlisted simulator container %s", name)
        return name, subprocess.Popen(command)

    def stop(self, name):
        subprocess.run(
            [self.docker_binary, "stop", "--time", "10", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


class SessionWorker(threading.Thread):
    def __init__(self, client, launcher, claim, poll_interval=2.0):
        super().__init__(daemon=True)
        self.client = client
        self.launcher = launcher
        self.claim = claim
        self.poll_interval = poll_interval
        self.session_id = str(claim["session"]["id"])
        self.environment = claim["environment"]
        self._stopping = threading.Event()
        self.container_name = ""
        self.process = None

    def request_stop(self):
        self._stopping.set()
        if self.container_name:
            self.launcher.stop(self.container_name)

    def run(self):
        try:
            self.client.report(self.session_id, "starting")
            self.container_name, self.process = self.launcher.start(self.environment)
            while not self._stopping.is_set():
                return_code = self.process.poll()
                if return_code is not None:
                    if return_code == 0:
                        self.client.report(self.session_id, "stopped")
                    else:
                        self.client.report(
                            self.session_id, "failed",
                            f"Godot container exited with code {return_code}.",
                        )
                    return
                state = self.client.report(self.session_id, "running")
                if state.get("desired_state") == "stopped":
                    self.request_stop()
                    break
                self._stopping.wait(self.poll_interval)
            if self.process and self.process.poll() is None:
                self.launcher.stop(self.container_name)
                self.process.wait(timeout=15)
            self.client.report(self.session_id, "stopped")
        except Exception as exc:
            LOG.exception("Simulation session %s failed", self.session_id)
            try:
                self.client.report(self.session_id, "failed", str(exc))
            except Exception:
                LOG.exception("Could not report runner failure for %s", self.session_id)


class Runner:
    def __init__(self, client, launcher, poll_interval=2.0):
        self.client = client
        self.launcher = launcher
        self.poll_interval = poll_interval
        self.workers = {}
        self.stopping = False

    def run_once(self):
        self.workers = {
            session_id: worker
            for session_id, worker in self.workers.items()
            if worker.is_alive()
        }
        claim = self.client.claim()
        if claim is None:
            return False
        session_id = str(claim["session"]["id"])
        if session_id in self.workers:
            raise RuntimeError(f"OES returned already-owned session {session_id}")
        worker = SessionWorker(self.client, self.launcher, claim, self.poll_interval)
        self.workers[session_id] = worker
        worker.start()
        return True

    def serve_forever(self):
        while not self.stopping:
            try:
                claimed = self.run_once()
            except Exception:
                LOG.exception("Unable to claim a simulation session")
                claimed = False
            if not claimed:
                time.sleep(self.poll_interval)
        for worker in self.workers.values():
            worker.request_stop()
        for worker in self.workers.values():
            worker.join(timeout=20)


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    base_url = os.getenv("OES_URL", "http://127.0.0.1:8010")
    token = os.getenv("SIMULATOR_RUNNER_TOKEN", "")
    if not token:
        raise SystemExit("SIMULATOR_RUNNER_TOKEN is required")
    identity = os.getenv("RUNNER_IDENTITY", os.uname().nodename)
    image = os.getenv("SIMULATOR_IMAGE", "terminal-scms/godot-simulator:local")
    network = os.getenv("SIMULATOR_DOCKER_NETWORK", "terminal-scms")
    poll_interval = float(os.getenv("RUNNER_POLL_INTERVAL", "2"))
    runner = Runner(
        OESClient(base_url, token, identity),
        DockerLauncher(image, network),
        poll_interval,
    )

    def stop(_signum, _frame):
        runner.stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    runner.serve_forever()


if __name__ == "__main__":
    main()
