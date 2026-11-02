#!/usr/bin/env python3
import json
import logging
import os
import signal
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid


LOG = logging.getLogger("terminal-simulator-runner")
ERROR_DETAIL_LIMIT = 300
# OES rejects a failure_reason longer than 2000 characters (HTTP 400). The cap
# includes the truncation marker and leaves headroom below the OES limit.
FAILURE_REASON_LIMIT = 1900
TRUNCATION_MARKER = "…[truncated, see runner log]"
# Reason sent with "stopped" when the runner itself shuts down (SIGTERM/SIGINT,
# e.g. a redeploy or `docker compose restart`). OES stores it on the session,
# so a cleanly ended session doesn't keep the NDEATH "offline" text.
SHUTDOWN_STOP_REASON = "stopped: runner shutdown"
def bound_failure_reason(reason):
    """Cap a failure_reason for OES; a cut is visible (marker), never silent.

    The full text (e.g. a crash traceback) is only in the runner log.
    """
    reason = reason or ""
    if len(reason) <= FAILURE_REASON_LIMIT:
        return reason
    return reason[:FAILURE_REASON_LIMIT - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


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
    # TCL-034 (TCL-011 hand-off design §6.1). SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED
    # is retired: the RTG no-show is always on.
    "SIMULATOR_TIMING_PROFILE",
    "SIMULATOR_QC_NO_SHOW_TIMEOUT_S",
    "SIMULATOR_RTG_NO_SHOW_TIMEOUT_S",
    "SIMULATOR_LEG_STALL_TIMEOUT_S",
    "SIMULATOR_COMMAND_TTL_S",
    "SIMULATOR_SCENARIO_ID",
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
            # Keep errors short: a debug-mode OES answers with a full HTML page
            # (request metadata included) that must not end up in logs or in a
            # failure_reason sent back to OES.
            detail = " ".join(detail.split())[:ERROR_DETAIL_LIMIT]
            raise RuntimeError(f"OES HTTP {exc.code}: {detail}") from exc

    def bootstrap_status(self, session_id, token):
        """Check whether a saved claim can still be resumed after a runner restart.

        Returns ``"ok"`` when OES still serves the bootstrap for this token,
        ``"stopping"`` when OES wants the session stopped, and ``"gone"`` when the
        token expired or the session no longer exists (e.g. after a reset).
        Network errors propagate, so the caller retries later.
        """
        request = urllib.request.Request(
            f"{self.base_url}/api/simulation-sessions/{session_id}/bootstrap/",
            method="GET",
            headers={"Accept": "application/json", "X-Simulation-Token": token},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                return "ok"
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                detail = exc.read().decode("utf-8", errors="replace")
                return "stopping" if "stopping" in detail else "gone"
            raise

    def claim(self):
        return self._request("POST", "/api/simulation-sessions/claim/", {
            "runner_identity": self.runner_identity,
        })

    def report(self, session_id, status, failure_reason=""):
        # Every path that sends a failure_reason goes through here.
        return self._request(
            "POST",
            f"/api/simulation-sessions/{session_id}/runner-status/",
            {
                "runner_identity": self.runner_identity,
                "status": status,
                "failure_reason": bound_failure_reason(failure_reason),
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
        validate_launch_environment(environment)
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


def validate_launch_environment(environment):
    unknown = set(environment) - ALLOWED_ENVIRONMENT
    missing = REQUIRED_ENVIRONMENT - set(environment)
    if unknown or missing:
        raise ValueError(
            f"Invalid launch environment; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )


class ProcessLauncher:
    """Runs headless Godot as a child process of the runner (compose service).

    Used inside the ``simulator-runner`` container, which carries both the
    runner and the Godot project, so no Docker socket is needed anywhere. The
    child gets only the allowlisted claim environment plus PATH and a
    per-session HOME/XDG_DATA_HOME, so Godot's ``user://`` checkpoint and
    result journal are kept per session on the data volume and survive a
    runner restart.
    """

    SCENE = "res://source/headless/HeadlessSimulator.tscn"

    def __init__(self, godot_binary="godot", project_path="/app", data_root="/data/sessions",
                 stop_timeout=10):
        self.godot_binary = godot_binary
        self.project_path = project_path
        self.data_root = data_root
        self.stop_timeout = stop_timeout
        self.processes = {}

    def session_home(self, session_id):
        return os.path.join(self.data_root, str(uuid.UUID(session_id)))

    def command(self, environment):
        validate_launch_environment(environment)
        session_id, _site_id = DockerLauncher._identity(environment)
        name = f"terminal-scms-sim-{session_id.replace('-', '')[:16]}"
        home = self.session_home(session_id)
        child_environment = {
            key: environment[key] for key in sorted(ALLOWED_ENVIRONMENT) if key in environment
        }
        child_environment.update({
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": home,
            "XDG_DATA_HOME": os.path.join(home, "data"),
            "XDG_CONFIG_HOME": os.path.join(home, "config"),
            "XDG_CACHE_HOME": os.path.join(home, "cache"),
        })
        command = [self.godot_binary, "--headless", "--path", self.project_path, self.SCENE]
        return name, command, child_environment

    def start(self, environment):
        name, command, child_environment = self.command(environment)
        os.makedirs(self.data_root, exist_ok=True)
        # The session home holds Godot's checkpoint and Sparkplug session state.
        os.makedirs(child_environment["HOME"], mode=0o700, exist_ok=True)
        os.chmod(child_environment["HOME"], 0o700)
        os.makedirs(child_environment["XDG_DATA_HOME"], mode=0o700, exist_ok=True)
        LOG.info("Starting allowlisted Godot process %s", name)
        process = subprocess.Popen(command, env=child_environment)
        self.processes[name] = process
        return name, process

    def stop(self, name):
        process = self.processes.get(name)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=self.stop_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def forget(self, session_id):
        """Remove a finished session's data directory (checkpoint, journal, logs)."""
        shutil.rmtree(self.session_home(session_id), ignore_errors=True)


class SessionStore:
    """Claims the runner owns, persisted so a restarted runner never orphans one.

    A claim is written (mode 600, it holds the bootstrap token) before Godot
    starts and removed once the session has ended and been reported. Each
    record carries a ``state``: ``running`` while Godot should run, or the
    final ``stopped``/``failed`` outcome, written as soon as it is known and
    before OES is told. On start-up the runner re-reports a saved final
    outcome as is (a cleanly stopped session stays stopped, however long the
    runner was down), resumes a ``running`` claim that OES still serves, and
    reports the rest as failed, so OES releases the session (hand-offs closed,
    reset and start no longer blocked).
    """

    RUNNING = "running"
    FINAL_STATES = ("stopped", "failed")

    def __init__(self, directory):
        self.directory = directory

    def _path(self, session_id):
        return os.path.join(self.directory, f"{uuid.UUID(session_id)}.json")

    def save(self, claim, state=RUNNING, failure_reason=""):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        session_id = str(claim["session"]["id"])
        path = self._path(session_id)
        temporary = f"{path}.tmp"
        record = {
            "session": {"id": session_id},
            "environment": claim["environment"],
            "state": state,
            "failure_reason": failure_reason,
        }
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def mark(self, claim, state, failure_reason=""):
        """Persist a session's final outcome before it is reported to OES."""
        if state not in self.FINAL_STATES:
            raise ValueError(f"Not a final session state: {state}")
        self.save(claim, state, failure_reason)

    def remove(self, session_id):
        try:
            os.remove(self._path(session_id))
        except FileNotFoundError:
            pass

    def load_all(self):
        if not os.path.isdir(self.directory):
            return []
        claims = []
        for entry in sorted(os.listdir(self.directory)):
            if not entry.endswith(".json"):
                continue
            path = os.path.join(self.directory, entry)
            try:
                with open(path, encoding="utf-8") as handle:
                    claim = json.load(handle)
                str(uuid.UUID(str(claim["session"]["id"])))
                claim["environment"]["SIMULATOR_TOKEN"]
                claim.setdefault("state", self.RUNNING)
                claim.setdefault("failure_reason", "")
                if claim["state"] not in (self.RUNNING,) + self.FINAL_STATES:
                    raise ValueError(claim["state"])
            except (OSError, ValueError, KeyError, TypeError):
                LOG.warning("Discarding unreadable runner state file %s", entry)
                os.remove(path)
                continue
            claims.append(claim)
        return claims


class SessionWorker(threading.Thread):
    def __init__(self, client, launcher, claim, poll_interval=2.0, store=None):
        super().__init__(daemon=True)
        self.client = client
        self.launcher = launcher
        self.claim = claim
        self.poll_interval = poll_interval
        self.store = store
        self.session_id = str(claim["session"]["id"])
        self.environment = claim["environment"]
        self._stopping = threading.Event()
        self.stop_reason = ""
        self.container_name = ""
        self.process = None
        self.final_reported = False

    def request_stop(self, reason=""):
        if not self._stopping.is_set():
            # A requested stop (OES or SIGTERM) is final: record it before
            # touching Godot, so a runner killed now still reports "stopped".
            # ``reason`` (e.g. SHUTDOWN_STOP_REASON) goes to OES with it.
            self.stop_reason = reason
            self._mark("stopped", reason)
        self._stopping.set()
        if self.container_name:
            self.launcher.stop(self.container_name)

    # Delays between attempts to report the final status (seconds). A final
    # status that still cannot be reported keeps the saved claim, so the next
    # runner start releases the session instead of orphaning it.
    FINAL_RETRY_DELAYS = (1, 2, 4, 8)

    def _mark(self, status, failure_reason=""):
        if self.store is None:
            return
        try:
            self.store.mark(self.claim, status, bound_failure_reason(failure_reason))
        except OSError:
            LOG.exception("Could not persist %s for %s", status, self.session_id)

    def run(self):
        status, failure_reason = self._run()
        if self._stopping.is_set() and status == "failed":
            # The stop was requested; an error while stopping does not make
            # the session a failure.
            LOG.warning("Error while stopping %s ignored: %s", self.session_id, failure_reason)
            status, failure_reason = "stopped", ""
        if status == "stopped" and not failure_reason:
            failure_reason = self.stop_reason
        self._mark(status, failure_reason)
        self._report_final(status, failure_reason)
        if self.final_reported:
            self._forget()
        else:
            LOG.warning("Keeping runner state for %s: final status not reported", self.session_id)

    def _forget(self):
        if self.store is not None:
            self.store.remove(self.session_id)
        forget = getattr(self.launcher, "forget", None)
        if callable(forget):
            forget(self.session_id)

    def _report_final(self, status, failure_reason=""):
        failure_reason = bound_failure_reason(failure_reason)
        delays = list(self.FINAL_RETRY_DELAYS)
        while True:
            try:
                if failure_reason:
                    self.client.report(self.session_id, status, failure_reason)
                else:
                    self.client.report(self.session_id, status)
                self.final_reported = True
                return
            except Exception as exc:
                if not delays:
                    LOG.error("Could not report %s for %s: %s", status, self.session_id, exc)
                    return
                LOG.warning("Could not report %s for %s (retrying): %s", status, self.session_id, exc)
                time.sleep(delays.pop(0))

    def _run(self):
        """Run Godot until it exits or OES asks for a stop; return the final status."""
        try:
            self.client.report(self.session_id, "starting")
            self.container_name, self.process = self.launcher.start(self.environment)
            while not self._stopping.is_set():
                return_code = self.process.poll()
                if return_code is not None:
                    if return_code == 0:
                        return "stopped", ""
                    return "failed", f"Godot container exited with code {return_code}."
                try:
                    state = self.client.report(self.session_id, "running") or {}
                except Exception as exc:
                    # A short OES outage must not kill a running simulation.
                    LOG.warning("Could not report running for %s: %s", self.session_id, exc)
                    state = {}
                if state.get("desired_state") == "stopped":
                    self.request_stop()
                    break
                self._stopping.wait(self.poll_interval)
            if self.process and self.process.poll() is None:
                self.launcher.stop(self.container_name)
                self.process.wait(timeout=15)
            return "stopped", ""
        except Exception:
            # The full traceback goes to the runner log; OES gets it bounded,
            # with a visible marker when cut (bound_failure_reason).
            trace = traceback.format_exc()
            LOG.error("Simulation session %s failed:\n%s", self.session_id, trace)
            if self.process is not None and self.process.poll() is None:
                try:
                    self.launcher.stop(self.container_name)
                except Exception:
                    LOG.exception("Could not stop Godot for %s", self.session_id)
            return "failed", trace.strip()


class Runner:
    def __init__(self, client, launcher, poll_interval=2.0, store=None):
        self.client = client
        self.launcher = launcher
        self.poll_interval = poll_interval
        self.store = store
        self.workers = {}
        self.stopping = False
        self.recovered = store is None

    def _start_worker(self, claim):
        session_id = str(claim["session"]["id"])
        worker = SessionWorker(
            self.client, self.launcher, claim, self.poll_interval, store=self.store,
        )
        self.workers[session_id] = worker
        worker.start()
        return worker

    def recover(self):
        """Resume or release the claims a previous runner process left behind.

        Runs before the first claim. Raises on network errors (OES not up yet),
        so ``serve_forever`` retries on the next poll; nothing is claimed until
        every saved session has been resumed or released.
        """
        for claim in self.store.load_all():
            session_id = str(claim["session"]["id"])
            if session_id in self.workers:
                continue
            if claim.get("state") in SessionStore.FINAL_STATES:
                # The outcome was decided before the restart (e.g. a clean
                # stop): report it as recorded, whatever the token says.
                outcome, reason = claim["state"], claim.get("failure_reason", "")
                LOG.info("Reporting recorded %s for simulation session %s after a runner restart",
                         outcome, session_id)
                self._release(session_id, outcome, reason)
                continue
            status = self.client.bootstrap_status(
                session_id, claim["environment"]["SIMULATOR_TOKEN"],
            )
            if status == "ok":
                LOG.info("Resuming simulation session %s after a runner restart", session_id)
                self._start_worker(claim)
                continue
            outcome = "stopped" if status == "stopping" else "failed"
            reason = "" if outcome == "stopped" else (
                "Simulator runner restarted; the session could not be resumed "
                "(bootstrap token expired or session gone)."
            )
            LOG.warning("Releasing simulation session %s as %s after a runner restart",
                        session_id, outcome)
            self._release(session_id, outcome, reason)
        self.recovered = True

    def _release(self, session_id, outcome, reason=""):
        try:
            if reason:
                self.client.report(session_id, outcome, reason)
            else:
                self.client.report(session_id, outcome)
        except RuntimeError as exc:
            # OES HTTP error, e.g. the session was wiped by a scenario reset.
            # Network errors propagate, so recovery is retried on the next poll.
            LOG.warning("OES did not accept the release of %s: %s", session_id, exc)
        self.store.remove(session_id)
        forget = getattr(self.launcher, "forget", None)
        if callable(forget):
            forget(session_id)

    def run_once(self):
        self.workers = {
            session_id: worker
            for session_id, worker in self.workers.items()
            if worker.is_alive()
        }
        if not self.recovered:
            self.recover()
        claim = self.client.claim()
        if claim is None:
            return False
        session_id = str(claim["session"]["id"])
        if session_id in self.workers:
            raise RuntimeError(f"OES returned already-owned session {session_id}")
        if self.store is not None:
            # Persist before launching, so a crash in between is still recoverable.
            self.store.save(claim)
        self._start_worker(claim)
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
            worker.request_stop(SHUTDOWN_STOP_REASON)
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
    poll_interval = float(os.getenv("RUNNER_POLL_INTERVAL", "2"))
    mode = os.getenv("RUNNER_LAUNCH_MODE", "docker").strip().lower()
    if mode == "process":
        # Compose service: Godot runs as a child process in this container.
        data_root = os.getenv("RUNNER_DATA_DIR", "/data")
        launcher = ProcessLauncher(
            godot_binary=os.getenv("GODOT_BINARY", "godot"),
            project_path=os.getenv("GODOT_PROJECT_PATH", "/app"),
            data_root=os.path.join(data_root, "sessions"),
        )
        store = SessionStore(os.path.join(data_root, "runner"))
    elif mode == "docker":
        launcher = DockerLauncher(
            os.getenv("SIMULATOR_IMAGE", "terminal-scms/godot-simulator:local"),
            os.getenv("SIMULATOR_DOCKER_NETWORK", "terminal-scms"),
        )
        state_dir = os.getenv("RUNNER_STATE_DIR", "")
        store = SessionStore(state_dir) if state_dir else None
    else:
        raise SystemExit(f"RUNNER_LAUNCH_MODE must be 'process' or 'docker', not {mode!r}")
    if not os.getenv("RUNNER_IDENTITY") and store is not None:
        LOG.warning("RUNNER_IDENTITY is not set; a restarted runner can only resume its "
                    "sessions if the identity stays the same.")
    runner = Runner(OESClient(base_url, token, identity), launcher, poll_interval, store)

    def stop(_signum, _frame):
        runner.stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    runner.serve_forever()


if __name__ == "__main__":
    main()
