#!/usr/bin/env python3
"""TCL-007: CI end-to-end runner for the S1 slice of ``import_v1``.

Runs against a live Docker Compose stack that has just been reset with
``make scenario-reset SCENARIO=import_v1`` and a host-side simulator runner
(``apps/simulator-runner/runner.py``) that launches the Godot image.

What it does, in order:

1. Pre-flight (untimed setup): OES health, the Site projection is ``ready``,
   start a simulation session through the OES API, wait until it is
   ``running`` and every seeded CHE is connected and available.
2. Subscribes to the UNS business root ``scms/v2/#`` (before any trigger).
3. Runs the S1 slice steps (docs/demo/S1_checklist.md section 3,
   docs/scenarios/import_v1.md steps 3 and 4) and checks, per step, that the
   expected business events arrive **in strict order** (D-037) and that the
   step completes within ``--step-limit`` seconds (default 5 s, D-037).
4. Writes a per-step PASS/FAIL summary (stdout, JSON, Markdown and, on GitHub
   Actions, ``$GITHUB_STEP_SUMMARY``). Exit code 0 only if every step passed.

Timing rule (documented in tests/e2e/README.md): a step's clock starts when
its trigger is issued (the CLI command or HTTP request; the trigger's own
duration is included and also reported separately), or, for a step driven by
the simulator alone, when the previous step's last expected event arrived.
The step passes when all of its expected events have arrived, in order, no
later than ``start + step_limit``.

The runner never reads ``.env``; tokens come from the environment
(``OES_API_TOKEN``). It only talks to localhost ports published by compose.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_SCHEMA_PATH = REPO_ROOT / "contracts/schemas/scms.execution.event.v1.schema.json"
FAILURE_EVENTS = {"handoff_exception", "leg_failed", "order_cancelled"}


# --------------------------------------------------------------------------
# Event model and matching (pure logic, unit-tested in test_s1_slice.py)
# --------------------------------------------------------------------------

@dataclass
class UnsMessage:
    topic: str
    payload: dict
    received_at: float  # time.monotonic()

    @property
    def schema(self) -> str:
        return str(self.payload.get("schema", ""))

    @property
    def data(self) -> dict:
        data = self.payload.get("data")
        return data if isinstance(data, dict) else {}

    @property
    def event(self) -> str:
        if self.schema == "scms.order.released.v1":
            return "order.released"
        return str(self.data.get("event", ""))

    def describe(self, leg_seq: dict[int, int] | None = None) -> str:
        if self.event == "order.released":
            return f"order.released(order {self.data.get('order', {}).get('id')})"
        data = self.data
        parts = []
        leg = data.get("leg_id")
        if leg is not None:
            seq = (leg_seq or {}).get(int(leg))
            parts.append(f"leg {seq}" if seq else f"leg_id {leg}")
        if data.get("from_che_name") or data.get("to_che_name"):
            parts.append(f"{data.get('from_che_name')}->{data.get('to_che_name')}")
        elif data.get("che_name"):
            parts.append(str(data["che_name"]))
        return f"{self.event}({', '.join(parts)})"


@dataclass
class Expect:
    """One expected business event. ``che`` may end in ``*`` (prefix match)."""
    event: str
    leg: int | None = None          # leg sequence (1 QC, 2 TT, 3 RTG)
    che: str | None = None
    from_che: str | None = None
    to_che: str | None = None
    extra: Callable[[UnsMessage], str | None] | None = None  # returns an error or None

    def label(self) -> str:
        parts = []
        if self.leg is not None:
            parts.append(f"leg {self.leg}")
        if self.from_che or self.to_che:
            parts.append(f"{self.from_che}->{self.to_che}")
        elif self.che:
            parts.append(self.che)
        return f"{self.event}({', '.join(parts)})"


def _che_matches(pattern: str | None, value) -> bool:
    if pattern is None:
        return True
    value = "" if value is None else str(value)
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def match_error(expect: Expect, message: UnsMessage, leg_seq: dict[int, int]) -> str | None:
    """None if ``message`` satisfies ``expect``, else a short reason."""
    if message.event != expect.event:
        return f"expected {expect.label()}, got {message.describe(leg_seq)}"
    data = message.data
    if expect.leg is not None:
        leg_id = data.get("leg_id")
        seq = leg_seq.get(int(leg_id)) if leg_id is not None else None
        if seq != expect.leg:
            return f"expected {expect.label()}, got {message.describe(leg_seq)}"
    if not _che_matches(expect.che, data.get("che_name")):
        return f"expected {expect.label()}, got che_name={data.get('che_name')}"
    if not _che_matches(expect.from_che, data.get("from_che_name")):
        return f"expected {expect.label()}, got from_che_name={data.get('from_che_name')}"
    if not _che_matches(expect.to_che, data.get("to_che_name")):
        return f"expected {expect.label()}, got to_che_name={data.get('to_che_name')}"
    if expect.extra is not None:
        return expect.extra(message)
    return None


@dataclass
class StepResult:
    step: str
    title: str
    trigger: str
    expected: list[str]
    observed: list[str] = field(default_factory=list)
    passed: bool = False
    elapsed_s: float | None = None
    trigger_s: float | None = None
    limit_s: float = 5.0
    error: str = ""
    checks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "step": self.step, "title": self.title, "trigger": self.trigger,
            "expected": self.expected, "observed": self.observed,
            "passed": self.passed, "elapsed_s": self.elapsed_s,
            "trigger_s": self.trigger_s, "limit_s": self.limit_s,
            "error": self.error, "checks": self.checks,
        }


class EventStream:
    """Thread-safe, ordered stream of business messages for one order.

    The MQTT thread calls :meth:`push`; the runner consumes with
    :meth:`next_message`. Messages for other orders or other topics are kept
    in ``ignored`` for the report but never matched.
    """

    def __init__(self):
        self._queue: "queue.Queue[UnsMessage]" = queue.Queue()
        self.all_messages: list[UnsMessage] = []
        self.ignored: list[UnsMessage] = []
        self.order_id: int | None = None
        self.late: UnsMessage | None = None
        self.leg_seq: dict[int, int] = {}
        self._lock = threading.Lock()

    def push(self, message: UnsMessage) -> None:
        with self._lock:
            self.all_messages.append(message)
        self._queue.put(message)

    def relevant(self, message: UnsMessage) -> bool:
        if message.schema == "scms.order.released.v1":
            return True
        if message.schema != "scms.execution.event.v1":
            return False
        if self.order_id is None:
            return True
        return message.data.get("order_id") == self.order_id

    def next_message(self, deadline: float) -> UnsMessage | None:
        # Lateness is judged on the arrival time (``received_at``), not on when
        # the runner gets round to reading, so a queued on-time event passes.
        while True:
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    message = self._queue.get(timeout=remaining)
                except queue.Empty:
                    return None
            if message.received_at > deadline:
                # Put nothing back: the step has failed on time anyway.
                self.late = message
                return None
            if self.relevant(message):
                return message
            self.ignored.append(message)

    def learn_order(self, released: UnsMessage) -> None:
        order = released.data.get("order") or {}
        self.order_id = int(order["id"]) if order.get("id") is not None else None
        for leg in released.data.get("legs") or []:
            self.leg_seq[int(leg["id"])] = int(leg["sequence"])


def run_expectations(
    step: StepResult, stream: EventStream, expectations: Iterable[Expect],
    start: float, on_message: Callable[[UnsMessage], str | None] | None = None,
) -> StepResult:
    """Consume events in order until every expectation is met or time is up."""
    deadline = start + step.limit_s
    for expect in expectations:
        message = stream.next_message(deadline)
        if message is None:
            step.error = (
                f"timeout: {expect.label()} not seen within {step.limit_s:g} s "
                f"of the step start"
            )
            late = stream.late
            if late is not None and stream.relevant(late):
                step.error += (
                    f" (next event {late.describe(stream.leg_seq)} arrived at "
                    f"{late.received_at - start:.3f} s)"
                )
            step.elapsed_s = round(time.monotonic() - start, 3)
            return step
        step.observed.append(message.describe(stream.leg_seq))
        if message.event in FAILURE_EVENTS and message.event != expect.event:
            step.error = f"failure event on the UNS: {message.describe(stream.leg_seq)} {message.data}"
            step.elapsed_s = round(message.received_at - start, 3)
            return step
        error = match_error(expect, message, stream.leg_seq)
        if error is None and on_message is not None:
            error = on_message(message)
        if error:
            step.error = f"order/content mismatch: {error}"
            step.elapsed_s = round(message.received_at - start, 3)
            return step
        step.elapsed_s = round(message.received_at - start, 3)
    step.passed = True
    return step


# --------------------------------------------------------------------------
# Optional JSON Schema validation of execution events (contracts/)
# --------------------------------------------------------------------------

def load_execution_validator():
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return None
    schema = json.loads(EXECUTION_SCHEMA_PATH.read_text())
    cls = jsonschema.validators.validator_for(schema)
    return cls(schema)


def schema_check(validator, message: UnsMessage) -> str | None:
    if validator is None or message.schema != "scms.execution.event.v1":
        return None
    errors = sorted(validator.iter_errors(message.payload), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        path = "/".join(str(p) for p in first.path) or "<root>"
        return f"{message.describe()} fails scms.execution.event.v1 at {path}: {first.message}"
    return None


# --------------------------------------------------------------------------
# Live stack clients
# --------------------------------------------------------------------------

class OES:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["X-OES-Token"] = self.token
        req = urllib.request.Request(self.base_url + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode() or "null"
                return response.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"raw": raw[:500]}

    def list(self, path: str) -> list:
        status, body = self.request("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path} -> HTTP {status}: {body}")
        if isinstance(body, dict) and "results" in body:
            return body["results"]
        return body or []


def wait_until(predicate: Callable[[], object], timeout: float, interval: float = 1.0, what: str = ""):
    deadline = time.monotonic() + timeout
    last_exc = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:  # noqa: BLE001 - reported on timeout
            last_exc = exc
        time.sleep(interval)
    raise TimeoutError(f"setup timeout after {timeout:g} s waiting for {what}" + (f" ({last_exc})" if last_exc else ""))


def start_mqtt(host: str, port: int, root: str, stream: EventStream):
    import paho.mqtt.client as mqtt

    connected = threading.Event()
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"tcl-007-e2e-{os.getpid()}",
        protocol=mqtt.MQTTv311,
    )

    def on_connect(client, _userdata, _flags, reason_code, _properties):
        if not reason_code.is_failure:
            client.subscribe(f"{root}/#", qos=1)

    def on_subscribe(*_args):
        connected.set()

    def on_message(_client, _userdata, msg):
        received = time.monotonic()
        if msg.retain:
            return  # business events are never retained (UNS_CONTRACT.md)
        try:
            payload = json.loads(msg.payload.decode())
        except (UnicodeDecodeError, ValueError):
            return
        if isinstance(payload, dict):
            stream.push(UnsMessage(msg.topic, payload, received))

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.connect(host, port, keepalive=30)
    client.loop_start()
    if not connected.wait(15):
        raise RuntimeError(f"could not subscribe to {root}/# on {host}:{port}")
    return client


def run_cli(command: list[str], timeout: float = 60.0) -> tuple[int, str, float]:
    started = time.monotonic()
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT)
    elapsed = time.monotonic() - started
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output, elapsed


# --------------------------------------------------------------------------
# The S1 slice
# --------------------------------------------------------------------------

EXPECTED_CHES = ("QC01", "TT01", "TT02", "RTG01")


def s1_plan(include_step4: bool):
    """(step id, title, trigger kind, expectations). Spec AC3.4 and AC4.3 order."""
    plan = [
        ("3a", "Release the discharge order", "minitos-release", [
            Expect("order.released", extra=_released_has_three_legs),
        ]),
        ("3b", "Dispatch QC + TT together (paired hand-off H1)", "oes-dispatch", [
            Expect("leg_dispatched", leg=1, che="QC01"),
            Expect("leg_dispatched", leg=2, che="TT*"),
        ]),
        ("3c", "QC01 picks the container from the vessel (Godot)", "simulator", [
            Expect("leg_started", leg=1, che="QC01"),
            Expect("container_picked_up", leg=1, che="QC01"),
        ]),
        ("3d", "QC01 -> TT hand-off (Godot, fast timings)", "simulator", [
            Expect("leg_started", leg=2, che="TT*"),
            Expect("container_transferred", from_che="QC01", to_che="TT*"),
            Expect("leg_completed", leg=1, che="QC01"),
        ]),
    ]
    if include_step4:
        plan.append(("4", "TT -> RTG01, RTG stacks in BLOCK01 (2nd dispatch, Q18)", "oes-dispatch", [
            Expect("leg_dispatched", leg=3, che="RTG01"),
            Expect("leg_started", leg=3, che="RTG01"),
            Expect("container_transferred", from_che="TT*", to_che="RTG01"),
            Expect("leg_completed", leg=2, che="TT*"),
            Expect("container_dropped", leg=3, che="RTG01"),
            Expect("leg_completed", leg=3, che="RTG01"),
        ]))
    return plan


def _released_has_three_legs(message: UnsMessage) -> str | None:
    legs = message.data.get("legs") or []
    seqs = sorted(int(leg.get("sequence", 0)) for leg in legs)
    if seqs != [1, 2, 3]:
        return f"order.released must carry legs 1..3, got sequences {seqs}"
    return None


class SliceRun:
    def __init__(self, args):
        self.args = args
        self.oes = OES(args.oes_url, os.environ.get("OES_API_TOKEN", ""))
        self.stream = EventStream()
        self.results: list[StepResult] = []
        self.setup_notes: list[str] = []
        self.validator = load_execution_validator()
        self.oes_order_id: int | None = None
        self.session_id: str | None = None
        self.tt_name: str | None = None
        self.compose = shlex.split(args.compose)

    # ---- setup --------------------------------------------------------
    def setup(self):
        a = self.args
        wait_until(lambda: self.oes.request("GET", "/health/")[0] == 200, 60, what="OES /health/")
        sites = wait_until(lambda: self.oes.list("/api/sites/?status=ready"), 60, what="a ready Site projection")
        site = sites[0]
        self.site_pk = site["id"]
        self.setup_notes.append(
            f"Site {site.get('enterprise_code')}/{site.get('code')} ready (revision {site.get('source_revision')})"
        )
        status, body = self.oes.request(
            "POST", f"/api/sites/{self.site_pk}/start-simulation/",
            {"simulator_version": "ci", "time_scale": a.time_scale},
        )
        if status not in (200, 201):
            raise RuntimeError(f"start-simulation -> HTTP {status}: {body}")
        self.session_id = str(body["id"])
        self.setup_notes.append(f"Simulation session {self.session_id} requested (time_scale {a.time_scale:g})")

        def session_running():
            status, body = self.oes.request("GET", f"/api/simulation-sessions/{self.session_id}/")
            if body.get("actual_state") == "failed":
                raise SystemExit(f"simulation session failed: {body.get('failure_reason')}")
            return status == 200 and body.get("actual_state") == "running"

        wait_until(session_running, a.setup_timeout, 2, "the simulation session to be running (is the runner up?)")

        def ches_ready():
            rows = self.oes.list(f"/api/equipment/?site_id={site.get('source_id')}")
            ready = {
                r.get("name") for r in rows
                if r.get("device_connected") and r.get("is_available") and not r.get("is_dispatched")
            }
            last_che_states[:] = [
                f"{r.get('name')}(connected={r.get('device_connected')}, available={r.get('is_available')}, "
                f"dispatched={r.get('is_dispatched')})"
                for r in rows
            ]
            return set(EXPECTED_CHES) <= ready

        last_che_states: list[str] = []
        try:
            wait_until(ches_ready, a.setup_timeout, 2, f"CHEs {', '.join(EXPECTED_CHES)} connected and available")
        except TimeoutError as exc:
            raise TimeoutError(f"{exc}; last seen: {', '.join(last_che_states) or 'no equipment rows'}") from None
        self.setup_notes.append(f"CHEs {', '.join(EXPECTED_CHES)} connected and available (AC2.3)")

    # ---- triggers -----------------------------------------------------
    def trigger_release(self, step: StepResult) -> bool:
        command = self.compose + [
            "exec", "-T", "minitos", "python", "manage.py", "scenario_step",
            self.args.scenario, "release-discharge",
        ]
        code, output, elapsed = run_cli(command)
        step.trigger_s = round(elapsed, 3)
        step.checks.append(f"`{' '.join(command[-4:])}` exit {code}: {output.splitlines()[-1] if output else ''}")
        if code != 0:
            step.error = f"release command failed (exit {code}): {output[-500:]}"
            return False
        return True

    def find_oes_order(self) -> int:
        def released():
            for order in self.oes.list("/api/orders/"):
                if self.stream.order_id is None or order.get("tos_order_id") == self.stream.order_id:
                    if order.get("status") in ("released", "in_progress"):
                        return order
            return None
        order = wait_until(released, self.args.step_limit, 0.2, "the OES order")
        return int(order["id"])

    def trigger_dispatch(self, step: StepResult) -> bool:
        if self.oes_order_id is None:
            self.oes_order_id = self.find_oes_order()
        started = time.monotonic()
        status, body = self.oes.request("POST", f"/api/orders/{self.oes_order_id}/dispatch-next/", {})
        step.trigger_s = round(time.monotonic() - started, 3)
        step.checks.append(f"POST dispatch-next (OES order {self.oes_order_id}) -> HTTP {status}")
        if status != 201 or not body.get("dispatched"):
            step.error = f"dispatch-next expected 201 dispatched:true, got {status}: {body}"
            return False
        return True

    def check_redispatch_noop(self, step: StepResult) -> None:
        """AC3.2: a second dispatch while H1 is in flight sends nothing new."""
        status, body = self.oes.request("POST", f"/api/orders/{self.oes_order_id}/dispatch-next/", {})
        ok = status == 200 and body.get("dispatched") is False and body.get("code")
        step.checks.append(
            f"re-dispatch while in flight -> HTTP {status} dispatched={body.get('dispatched')} "
            f"code={body.get('code')} ({'ok' if ok else 'FAIL'})"
        )
        if not ok:
            step.error = f"AC3.2: re-dispatch should be 200 dispatched:false, got {status}: {body}"

    # ---- steps --------------------------------------------------------
    def on_message(self, message: UnsMessage) -> str | None:
        if message.event == "order.released":
            self.stream.learn_order(message)
        if message.event == "leg_dispatched" and self.stream.leg_seq.get(int(message.data.get("leg_id", -1))) == 2:
            self.tt_name = message.data.get("che_name")
        if message.event == "container_transferred" and self.tt_name:
            names = {message.data.get("from_che_name"), message.data.get("to_che_name")}
            if self.tt_name not in names:
                return f"container_transferred uses {names}, but the dispatched TT was {self.tt_name}"
        if message.event in {"leg_started", "leg_completed"} and self.tt_name:
            if self.stream.leg_seq.get(int(message.data.get("leg_id", -1))) == 2 and message.data.get("che_name") != self.tt_name:
                return f"leg 2 event on {message.data.get('che_name')}, dispatched TT was {self.tt_name}"
        return schema_check(self.validator, message)

    def run_steps(self) -> None:
        last_event_at = time.monotonic()
        for step_id, title, trigger, expectations in s1_plan(self.args.include_step4):
            step = StepResult(
                step=step_id, title=title, trigger=trigger,
                expected=[e.label() for e in expectations], limit_s=self.args.step_limit,
            )
            self.results.append(step)
            if any(not r.passed for r in self.results[:-1]):
                step.error = "skipped: an earlier step failed"
                continue
            if trigger == "minitos-release":
                start = time.monotonic()
                if not self.trigger_release(step):
                    continue
            elif trigger == "oes-dispatch":
                if self.oes_order_id is None:
                    self.oes_order_id = self.find_oes_order()
                start = time.monotonic()
                if not self.trigger_dispatch(step):
                    continue
                if step_id == "3b":
                    # AC3.2 right away, while H1 is surely still in flight.
                    self.check_redispatch_noop(step)
                    if step.error:
                        continue
            else:
                start = last_event_at
            run_expectations(step, self.stream, expectations, start, self.on_message)
            if step.passed:
                last_event_at = start + (step.elapsed_s or 0)
        self.final_checks()

    def final_checks(self) -> None:
        if not all(r.passed for r in self.results):
            return
        step = StepResult(
            step="end", title="End state and whole-order invariants", trigger="checks",
            expected=["no extra events", "OES order state"], limit_s=self.args.step_limit,
        )
        self.results.append(step)
        start = time.monotonic()
        # Collect anything that is still arriving (an extra event is a failure).
        extra = []
        while True:
            message = self.stream.next_message(time.monotonic() + self.args.quiet_period)
            if message is None:
                break
            extra.append(message.describe(self.stream.leg_seq))
        if extra:
            step.error = f"unexpected extra business events after the last step: {extra}"
        want = "complete" if self.args.include_step4 else "in_progress"
        try:
            order = wait_until(
                lambda: (lambda o: o if o.get("status") == want else None)(
                    self.oes.request("GET", f"/api/orders/{self.oes_order_id}/")[1]
                ),
                self.args.step_limit, 0.2, f"OES order status {want}",
            )
            step.checks.append(f"OES order {self.oes_order_id} status {order.get('status')}")
        except TimeoutError as exc:
            step.error = step.error or str(exc)
        step.elapsed_s = round(time.monotonic() - start, 3)
        step.passed = not step.error

    def compare_with_previous(self) -> None:
        """D-016 rerun check: same events, same order, same CHEs as a prior run."""
        path = self.args.compare_with
        if not path or not all(r.passed for r in self.results):
            return
        step = StepResult(
            step="rerun", title=f"Same states and order as {path} (D-016, AC4.6)",
            trigger="compare", expected=["identical observed sequence per step"],
            limit_s=self.args.step_limit,
        )
        self.results.append(step)
        try:
            previous = json.loads(Path(path).read_text())
        except (OSError, ValueError) as exc:
            step.error = f"cannot read {path}: {exc}"
            return
        before = {s["step"]: s["observed"] for s in previous.get("steps", []) if s["step"] not in ("end", "rerun")}
        now = {r.step: r.observed for r in self.results if r.step not in ("end", "rerun")}
        diffs = [f"step {k}: {before.get(k)} != {now.get(k)}" for k in sorted(set(before) | set(now)) if before.get(k) != now.get(k)]
        step.error = "; ".join(diffs)
        step.passed = not diffs
        step.checks.append(f"compared {len(now)} steps")

    def teardown(self) -> None:
        if self.session_id and self.args.stop_simulation:
            status, body = self.oes.request("POST", f"/api/simulation-sessions/{self.session_id}/stop/", {})
            self.setup_notes.append(f"Stop simulation -> HTTP {status} ({body.get('actual_state') if isinstance(body, dict) else body})")

            def stopped():
                _, body = self.oes.request("GET", f"/api/simulation-sessions/{self.session_id}/")
                return isinstance(body, dict) and body.get("actual_state") in ("stopped", "failed")

            try:
                wait_until(stopped, 60, 1, "the simulation session to stop")
                self.setup_notes.append("Simulation session stopped (a following reset is allowed)")
            except TimeoutError as exc:
                self.setup_notes.append(str(exc))


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def render_markdown(results: list[StepResult], notes: list[str], fatal: str = "") -> str:
    lines = ["## TCL-007 S1 slice (import_v1)", ""]
    if fatal:
        lines += [f"**SETUP FAILED:** {fatal}", ""]
    for note in notes:
        lines.append(f"- {note}")
    lines += [
        "",
        "| Step | Result | Time (s) | Limit (s) | Trigger (s) | Expected | Observed | Detail |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        verdict = "PASS" if r.passed else ("SKIP" if r.error.startswith("skipped") else "FAIL")
        detail = "; ".join([*r.checks, r.error] if r.error else r.checks).replace("|", "\\|")
        lines.append(
            f"| {r.step} {r.title} | {verdict} | {'' if r.elapsed_s is None else r.elapsed_s} | "
            f"{r.limit_s:g} | {'' if r.trigger_s is None else r.trigger_s} | "
            f"{'<br>'.join(r.expected)} | {'<br>'.join(r.observed)} | {detail} |"
        )
    passed = bool(results) and not fatal and all(r.passed for r in results)
    lines += ["", f"**Overall: {'PASS' if passed else 'FAIL'}**", ""]
    return "\n".join(lines)


def write_reports(args, results, notes, stream: EventStream | None, fatal="") -> bool:
    passed = bool(results) and not fatal and all(r.passed for r in results)
    markdown = render_markdown(results, notes, fatal)
    print(markdown)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "s1_slice.md").write_text(markdown)
    (report_dir / "s1_slice.json").write_text(json.dumps({
        "passed": passed, "fatal": fatal, "notes": notes,
        "steps": [r.as_dict() for r in results],
    }, indent=2))
    if stream is not None:
        with (report_dir / "uns_messages.jsonl").open("w") as fh:
            t0 = stream.all_messages[0].received_at if stream.all_messages else 0
            for m in stream.all_messages:
                fh.write(json.dumps({"t": round(m.received_at - t0, 3), "topic": m.topic, "payload": m.payload}) + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(markdown + "\n")
    return passed


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--oes-url", default=os.environ.get("E2E_OES_URL", f"http://127.0.0.1:{os.environ.get('OES_HTTP_PORT', '8010')}"))
    p.add_argument("--mqtt-host", default=os.environ.get("E2E_MQTT_HOST", "127.0.0.1"))
    p.add_argument("--mqtt-port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--uns-root", default="scms/v2")
    p.add_argument("--scenario", default="import_v1")
    p.add_argument("--compose", default=os.environ.get("COMPOSE", "docker compose"))
    p.add_argument("--step-limit", type=float, default=float(os.environ.get("E2E_STEP_LIMIT_S", "5")),
                   help="D-037: per-step limit in seconds (do not raise to hide flakiness; open a ticket)")
    p.add_argument("--time-scale", type=float, default=float(os.environ.get("E2E_TIME_SCALE", "10")),
                   help="Simulation session time_scale (fast timing profile stand-in)")
    p.add_argument("--setup-timeout", type=float, default=180.0)
    p.add_argument("--quiet-period", type=float, default=1.0,
                   help="seconds to wait for stray events after the last step")
    p.add_argument("--no-step4", dest="include_step4", action="store_false")
    p.add_argument("--keep-simulation", dest="stop_simulation", action="store_false")
    p.add_argument("--compare-with", default="",
                   help="s1_slice.json of an earlier run after a reset: assert the same sequence (D-016)")
    p.add_argument("--report-dir", default=os.environ.get("E2E_REPORT_DIR", "e2e-report"))
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    run = SliceRun(args)
    client = None
    fatal = ""
    try:
        client = start_mqtt(args.mqtt_host, args.mqtt_port, args.uns_root, run.stream)
        run.setup_notes.append(f"Subscribed to {args.uns_root}/# on {args.mqtt_host}:{args.mqtt_port}")
        if run.validator is None:
            run.setup_notes.append("jsonschema not installed: execution events are NOT schema-validated")
        else:
            run.setup_notes.append("Execution events validated against contracts/schemas/scms.execution.event.v1")
        run.setup()
        run.run_steps()
        run.compare_with_previous()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - reported
        fatal = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            run.teardown()
        except Exception as exc:  # noqa: BLE001
            run.setup_notes.append(f"teardown error: {exc}")
        if client is not None:
            client.loop_stop()
            client.disconnect()
    return 0 if write_reports(args, run.results, run.setup_notes, run.stream, fatal) else 1


if __name__ == "__main__":
    sys.exit(main())
