#!/usr/bin/env python3
"""TCL-007: CI end-to-end runner for the S1 slice of ``import_v1``.

Runs against a live Docker Compose stack that has just been reset with
``make scenario-reset SCENARIO=import_v1``, whose visit was then moved to
InOperation with ``make scenario-step SCENARIO=import_v1 STEP=visit-in-operation``
(TCL-010: that creates the discharge orders; untimed setup), and the
``simulator-runner`` compose service (#29), which launches Godot as a child process.

What it does, in order:

1. Pre-flight (untimed setup): OES health, the Site projection is ``ready``,
   start a simulation session through the OES API, wait until it is
   ``running`` and every seeded CHE is connected and available.
2. Subscribes to the UNS business root ``scms/v2/#`` (before any trigger).
3. Runs the S1 slice steps (docs/demo/S1_checklist.md section 3,
   docs/scenarios/import_v1.md steps 3 and 4) and checks, per step, that the
   expected business events arrive **in strict order** (D-037 / D-050).
   Reaction-only steps (3a, 3b, 3c) must finish within ``--step-limit``
   wall-clock seconds (default 5 s, D-037). Steps with physical work (3d and
   4: TT drive, crane cycle) are asserted in sim time from
   ``data.occurred_at`` (the UNS field filled from the Sparkplug
   ``Event/TimeMs`` envelope stamp, contract §2.1) against a bound derived
   from the import_v1 layout, TT speed, and the effective crane timings
   published on the session NBIRTH (D-050; ``Sim/Timing/QcHold*``,
   ``RtgHold*``, and after TCL-034e ``RtgPositioningS``). Their wall-clock
   time is logged; the wait uses only a generous safety timeout, not the
   5 s limit. Step 4's second dispatch is still checked against the 5 s
   wall-clock reaction limit; the physical work after that is sim time.
4. Writes a per-step PASS/FAIL summary (stdout, JSON, Markdown and, on GitHub
   Actions, ``$GITHUB_STEP_SUMMARY``). Exit code 0 only if every step passed.

Timing rule (documented in tests/e2e/README.md): a step's clock starts when
its trigger is issued (the CLI command or HTTP request; the trigger's own
duration is included and also reported separately), or, for a step driven by
the simulator alone, when the previous step's last expected event arrived.
Reaction-only steps pass when all expected events have arrived, in order, no
later than ``start + step_limit`` wall-clock. Physical-work steps pass when
their envelope sim-time span is within the layout-derived bound.

The runner never reads ``.env``; tokens come from the environment
(``OES_API_TOKEN``). It only talks to localhost ports published by compose.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

_E2E_DIR = Path(__file__).resolve().parent
if str(_E2E_DIR) not in sys.path:
    sys.path.insert(0, str(_E2E_DIR))
from sparkplug_codec import (  # noqa: E402
    decode_payload,
    encode_rebirth_ncmd,
    metrics_by_name,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTION_SCHEMA_PATH = REPO_ROOT / "contracts/schemas/scms.execution.event.v1.schema.json"
FAILURE_EVENTS = {"handoff_exception", "leg_failed", "order_cancelled"}
# Execution events whose data.occurred_at is the simulator clock (import_v1
# epoch), not OES wall time. Dispatch and release are stamped with wall time.
SIM_CLOCK_EVENTS = {
    "leg_started", "container_picked_up", "container_transferred",
    "container_dropped", "leg_completed",
}
# D-050: steps that include physical work (TT drive, crane cycle).
PHYSICAL_STEPS = ("3d", "4")
# Slack on a drive term is exactly two sim ticks (GD review of #49, S1): the
# safe, speed-independent margin against tick alignment. Not a literal 0.2 s.
TICKS_OF_SLACK = 2
DEFAULT_STEP_MS = 100
# Added to (sim bound / time_scale) so a hung run still stops; not a D-050
# or D-037 assertion.
SAFETY_WALL_SLACK_S = 30.0
DEFAULT_TIMING_PROFILE = "fast"
IMPORT_V1_REL = Path("apps/minitos/minitos_core/scenarios/import_v1.py")
COMPOSE_REL = Path("compose.yaml")
HEADLESS_REL = Path("clients/godot-open-terminal/source/headless/HeadlessSimulator.gd")
SIMCLOCK_REL = Path("clients/godot-open-terminal/source/headless/SimClock.gd")
# Env keys the simulator applies on top of the profile. Effective holds come
# from NBIRTH, so these do not need to be parsed; they are listed so a live
# run can note that overrides were in effect.
HOLD_OVERRIDE_ENV_KEYS = (
    "SIMULATOR_QC_WAIT_MIN_S", "SIM_QC_WAIT_MIN_S",
    "SIMULATOR_QC_WAIT_MAX_S", "SIM_QC_WAIT_MAX_S",
    "SIMULATOR_QC_HOLD_WAIT_MIN_S", "SIM_QC_HOLD_WAIT_MIN_S",
    "SIMULATOR_QC_HOLD_WAIT_MAX_S", "SIM_QC_HOLD_WAIT_MAX_S",
    "SIMULATOR_RTG_WAIT_MIN_S", "SIM_RTG_WAIT_MIN_S",
    "SIMULATOR_RTG_WAIT_MAX_S", "SIM_RTG_WAIT_MAX_S",
    "SIMULATOR_RTG_HOLD_WAIT_MIN_S", "SIM_RTG_HOLD_WAIT_MIN_S",
    "SIMULATOR_RTG_HOLD_WAIT_MAX_S", "SIM_RTG_HOLD_WAIT_MAX_S",
)
NBIRTH_QC_HOLD = ("Sim/Timing/QcHoldMinS", "Sim/Timing/QcHoldMaxS")
NBIRTH_RTG_HOLD = ("Sim/Timing/RtgHoldMinS", "Sim/Timing/RtgHoldMaxS")
NBIRTH_POSITIONING = "Sim/Timing/RtgPositioningS"
NBIRTH_STEP_MS = "Sim/FixedStepMs"
NBIRTH_PROFILE = "Sim/Timing/Profile"
NBIRTH_TIME_SCALE = "Sim/TimeScale"
SPARKPLUG_NBIRTH_FILTER = "spBv1.0/+/NBIRTH/+"


# --------------------------------------------------------------------------
# D-050 layout-derived sim-time bounds (pure logic, unit-tested)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)


def point_along_line(start: Point, end: Point, index: int, count: int) -> Point:
    """Interface position along a line, matching sync_interface_positions.

    Positions sit at (index - 0.5) / count, with index 1-based. A single
    position is the midpoint; two positions sit at 1/4 and 3/4.
    """
    if count < 1:
        raise ValueError(f"interface position count must be >= 1, got {count}")
    t = (index - 0.5) / count
    return Point(start.x + t * (end.x - start.x), start.y + t * (end.y - start.y))


def drive_time_s(start: Point, end: Point, speed_mps: float) -> float:
    if speed_mps <= 0:
        raise ValueError(f"speed_mps must be > 0, got {speed_mps}")
    return start.distance_to(end) / speed_mps


def slack_s(step_ms: int | float) -> float:
    """Drive slack: two configured sim ticks, in seconds."""
    return TICKS_OF_SLACK * (float(step_ms) / 1000.0)


def tick_s(step_ms: int | float) -> float:
    return float(step_ms) / 1000.0


def _require_metric(metrics: dict, name: str) -> float:
    if name not in metrics or metrics[name] is None:
        raise ValueError(f"session NBIRTH is missing required metric {name}")
    try:
        return float(metrics[name])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"session NBIRTH metric {name} is not a number: {metrics[name]!r}") from exc


def timings_from_nbirth(metrics: dict) -> dict:
    """Effective crane timings from a decoded session NBIRTH.

    Holds are the values the simulator actually uses (profile plus any env
    overrides). ``RtgPositioningS`` is absent on main before TCL-034e and
    defaults to 0. ``Sim/FixedStepMs`` is used for the 2-tick slack when
    present.
    """
    qc = (_require_metric(metrics, NBIRTH_QC_HOLD[0]), _require_metric(metrics, NBIRTH_QC_HOLD[1]))
    rtg = (_require_metric(metrics, NBIRTH_RTG_HOLD[0]), _require_metric(metrics, NBIRTH_RTG_HOLD[1]))
    if qc[1] < qc[0] or rtg[1] < rtg[0]:
        raise ValueError(f"session NBIRTH hold ranges are inverted: qc={qc}, rtg={rtg}")
    positioning = 0.0
    if metrics.get(NBIRTH_POSITIONING) is not None:
        positioning = float(metrics[NBIRTH_POSITIONING])
    step_ms = None
    if metrics.get(NBIRTH_STEP_MS) is not None:
        step_ms = int(metrics[NBIRTH_STEP_MS])
        if step_ms <= 0:
            raise ValueError(f"session NBIRTH {NBIRTH_STEP_MS} must be > 0, got {step_ms}")
    profile = str(metrics[NBIRTH_PROFILE]).strip() if metrics.get(NBIRTH_PROFILE) else ""
    time_scale = None
    if metrics.get(NBIRTH_TIME_SCALE) is not None:
        time_scale = float(metrics[NBIRTH_TIME_SCALE])
    return {
        "qc_hold": (qc[0], qc[1]),
        "rtg_hold": (rtg[0], rtg[1]),
        "rtg_positioning_s": positioning,
        "step_ms": step_ms,
        "profile": profile or None,
        "time_scale": time_scale,
    }


def nbirth_fixture(
    *,
    profile: str,
    qc_hold: tuple[float, float],
    rtg_hold: tuple[float, float],
    positioning_s: float = 0.0,
    step_ms: int = DEFAULT_STEP_MS,
    time_scale: float = 10.0,
    include_positioning: bool = True,
) -> dict:
    """Build an NBIRTH-like metric map for self-tests (no broker)."""
    metrics = {
        NBIRTH_PROFILE: profile,
        NBIRTH_QC_HOLD[0]: qc_hold[0],
        NBIRTH_QC_HOLD[1]: qc_hold[1],
        NBIRTH_RTG_HOLD[0]: rtg_hold[0],
        NBIRTH_RTG_HOLD[1]: rtg_hold[1],
        NBIRTH_STEP_MS: step_ms,
        NBIRTH_TIME_SCALE: time_scale,
    }
    if include_positioning:
        metrics[NBIRTH_POSITIONING] = positioning_s
    return metrics


def parse_default_speed_mps(compose_text: str, headless_text: str = "") -> float:
    match = re.search(
        r"SIMULATOR_DEFAULT_SPEED_MPS:[^\n]*?:-([0-9.]+)", compose_text,
    )
    if match:
        return float(match.group(1))
    match = re.search(
        r"_default_speed_mps\s*:=\s*([0-9.]+)", headless_text,
    )
    if match:
        return float(match.group(1))
    raise ValueError(
        "could not read SIMULATOR_DEFAULT_SPEED_MPS default from compose.yaml "
        "or HeadlessSimulator.gd"
    )


def parse_fixed_step_ms(compose_text: str, simclock_text: str = "", env=None) -> int:
    """Tick length: env, then compose default, then SimClock.DEFAULT_STEP_MS."""
    env = os.environ if env is None else env
    for key in ("SIMULATOR_FIXED_STEP_MS", "SIM_FIXED_STEP_MS"):
        raw = env.get(key)
        if raw:
            value = int(raw)
            if value <= 0:
                raise ValueError(f"{key} must be > 0, got {raw!r}")
            return value
    match = re.search(r"SIMULATOR_FIXED_STEP_MS:[^\n]*?:-(\d+)", compose_text)
    if match:
        return int(match.group(1))
    match = re.search(r"const\s+DEFAULT_STEP_MS\s*:=\s*(\d+)", simclock_text)
    if match:
        return int(match.group(1))
    return DEFAULT_STEP_MS


def hold_override_env_keys(env=None) -> list[str]:
    env = os.environ if env is None else env
    return [key for key in HOLD_OVERRIDE_ENV_KEYS if env.get(key)]


def assert_layout_speed_is_default(text: str) -> None:
    """Fail if the seed sets a CHE driving speed the bound would ignore (S2)."""
    for match in re.finditer(r"driving_speed_empty_kmh\s*=\s*([0-9.]+)", text):
        if float(match.group(1)) > 0:
            raise ValueError(
                "import_v1 seed sets driving_speed_empty_kmh="
                f"{match.group(1)}; the D-050 bound uses "
                "SIMULATOR_DEFAULT_SPEED_MPS and would silently disagree "
                "with the simulator (HeadlessSimulator._che_speed_mps)"
            )


def parse_import_v1_layout(text: str) -> tuple[dict[str, Point], Point, Point]:
    """CHE start points, QC01.LANE.01 and BLOCK01.TL.01 in local metres."""
    starts: dict[str, Point] = {}
    for match in re.finditer(
        r"\('((?:TT|QC|RTG)\d+)',\s*'[^']+',\s*([0-9.]+),\s*([0-9.]+),",
        text,
    ):
        starts[match.group(1)] = Point(float(match.group(2)), float(match.group(3)))
    if "TT01" not in starts or "TT02" not in starts:
        raise ValueError(f"import_v1.py: missing TT start points, found {sorted(starts)}")

    qc = re.search(
        r"code='QC01\.LANE'.*?_line\(\s*location,\s*\(([-\d.]+),\s*([-\d.]+)\)\s*,\s*\(([-\d.]+),\s*([-\d.]+)\)\s*\).*?"
        r"sync_interface_positions\(\s*qc_lane,\s*(\d+)\s*\)",
        text,
        re.S,
    )
    if not qc:
        raise ValueError("import_v1.py: could not parse QC01.LANE geometry and position count")
    qc_lane = point_along_line(
        Point(float(qc.group(1)), float(qc.group(2))),
        Point(float(qc.group(3)), float(qc.group(4))),
        1,
        int(qc.group(5)),
    )

    block = re.search(
        r"code=f'\{EXCEPTION_BLOCK_NAME\}\.TL'.*?_line\(\s*location,\s*\(([-\d.]+),\s*([-\d.]+)\)\s*,\s*\(([-\d.]+),\s*([-\d.]+)\)\s*\).*?"
        r"sync_interface_positions\(\s*block_lane,\s*(\d+)\s*\)",
        text,
        re.S,
    )
    if not block:
        raise ValueError("import_v1.py: could not parse BLOCK01.TL geometry and position count")
    block_tl = point_along_line(
        Point(float(block.group(1)), float(block.group(2))),
        Point(float(block.group(3)), float(block.group(4))),
        1,
        int(block.group(5)),
    )
    return starts, qc_lane, block_tl


def _fmt_ticks(n: int, step_ms: int | float) -> str:
    return f"{n} tick{'s' if n != 1 else ''} ({n * tick_s(step_ms):g} s)"


@dataclass
class PhysicalBounds:
    """Layout-derived D-050 sim-time bounds for steps 3d and 4."""
    profile: str
    speed_mps: float
    qc_hold: tuple[float, float]
    rtg_hold: tuple[float, float]
    qc_lane: Point
    block_tl: Point
    tt_starts: dict[str, Point]
    time_scale: float
    step_ms: int = DEFAULT_STEP_MS
    rtg_positioning_s: float = 0.0

    @property
    def slack_s(self) -> float:
        return slack_s(self.step_ms)

    @property
    def tick_s(self) -> float:
        return tick_s(self.step_ms)

    def drive_s(self, start: Point, end: Point) -> float:
        return drive_time_s(start, end, self.speed_mps)

    def tt_drive_to_qc_s(self, tt_name: str | None) -> tuple[float, str, Point]:
        if tt_name and tt_name in self.tt_starts:
            start = self.tt_starts[tt_name]
            return self.drive_s(start, self.qc_lane), tt_name, start
        # Worst case of the seeded TTs until the dispatched one is known.
        name, start = max(
            self.tt_starts.items(),
            key=lambda item: self.drive_s(item[1], self.qc_lane),
        )
        return self.drive_s(start, self.qc_lane), name, start

    def bound_3d_s(self, tt_name: str | None = None) -> float:
        drive_s, _, _ = self.tt_drive_to_qc_s(tt_name)
        # QC hold and TT drive run in parallel; deposit is max(hold end, TT ready).
        # Measured from TT leg_started, so hold end - t(TT start) <= H_qc.
        return max(drive_s, self.qc_hold[1]) + self.slack_s

    def bound_4_s(
        self,
        h1_end: datetime | None = None,
        rtg_started: datetime | None = None,
    ) -> float:
        """Step 4: max(drive2 + 2 ticks, (RTG start - H1) + 1 tick + P) + H_rtg.

        Dispatch delay is not treated as layout work (M2). When the RTG
        ``leg_started`` stamp is not yet known, the arrival term is 0.
        """
        drive_s = self.drive_s(self.qc_lane, self.block_tl)
        drive_term = drive_s + self.slack_s
        arrival_s = 0.0
        if h1_end is not None and rtg_started is not None:
            arrival_s = (rtg_started - h1_end).total_seconds()
        position_term = arrival_s + self.tick_s + self.rtg_positioning_s
        return max(drive_term, position_term) + self.rtg_hold[1]

    def sim_bound_s(
        self,
        step_id: str,
        tt_name: str | None = None,
        h1_end: datetime | None = None,
        rtg_started: datetime | None = None,
    ) -> float:
        if step_id == "3d":
            return self.bound_3d_s(tt_name)
        if step_id == "4":
            return self.bound_4_s(h1_end=h1_end, rtg_started=rtg_started)
        raise ValueError(f"no D-050 sim bound for step {step_id}")

    def safety_wall_s(
        self,
        step_id: str,
        tt_name: str | None = None,
        h1_end: datetime | None = None,
        rtg_started: datetime | None = None,
    ) -> float:
        return (
            self.sim_bound_s(step_id, tt_name, h1_end=h1_end, rtg_started=rtg_started)
            / max(self.time_scale, 0.001)
            + SAFETY_WALL_SLACK_S
        )

    def formula_3d(self, tt_name: str | None = None) -> str:
        drive_s, used, start = self.tt_drive_to_qc_s(tt_name)
        who = used if (tt_name and tt_name in self.tt_starts) else f"worst-case {used}"
        dist = start.distance_to(self.qc_lane)
        return (
            f"max({who} drive {dist:.1f} m / {self.speed_mps:g} m/s = {drive_s:.3f} s, "
            f"QC hold <= {self.qc_hold[1]:g} s) + {_fmt_ticks(TICKS_OF_SLACK, self.step_ms)}"
        )

    def formula_4(
        self,
        h1_end: datetime | None = None,
        rtg_started: datetime | None = None,
    ) -> str:
        drive_s = self.drive_s(self.qc_lane, self.block_tl)
        dist = self.qc_lane.distance_to(self.block_tl)
        arrival_s = 0.0
        arrival_note = "0 s (dispatch not yet observed)"
        if h1_end is not None and rtg_started is not None:
            arrival_s = (rtg_started - h1_end).total_seconds()
            arrival_note = f"{arrival_s:g} s"
        position_term = arrival_s + self.tick_s + self.rtg_positioning_s
        return (
            f"max(QC01.LANE.01->BLOCK01.TL.01 drive {dist:.1f} m / {self.speed_mps:g} m/s "
            f"= {drive_s:.3f} s + {_fmt_ticks(TICKS_OF_SLACK, self.step_ms)}, "
            f"(RTG leg_started - H1) {arrival_note} + {_fmt_ticks(1, self.step_ms)} "
            f"+ RTG positioning {self.rtg_positioning_s:g} s = {position_term:.3f} s) "
            f"+ RTG hold <= {self.rtg_hold[1]:g} s"
        )

    def formula(
        self,
        step_id: str,
        tt_name: str | None = None,
        h1_end: datetime | None = None,
        rtg_started: datetime | None = None,
    ) -> str:
        if step_id == "3d":
            return self.formula_3d(tt_name)
        if step_id == "4":
            return self.formula_4(h1_end=h1_end, rtg_started=rtg_started)
        raise ValueError(f"no D-050 formula for step {step_id}")

    def setup_notes(self) -> list[str]:
        tt01 = self.tt_starts["TT01"]
        tt02 = self.tt_starts["TT02"]
        return [
            (
                f"D-050 bounds ({self.profile} profile, {self.speed_mps:g} m/s, "
                f"time_scale {self.time_scale:g}, tick {self.step_ms} ms): "
                f"QC01.LANE.01=({self.qc_lane.x:g},{self.qc_lane.y:g}), "
                f"BLOCK01.TL.01=({self.block_tl.x:g},{self.block_tl.y:g}), "
                f"TT01=({tt01.x:g},{tt01.y:g}), TT02=({tt02.x:g},{tt02.y:g}); "
                f"QC hold {self.qc_hold[0]:g}..{self.qc_hold[1]:g} s, "
                f"RTG hold {self.rtg_hold[0]:g}..{self.rtg_hold[1]:g} s, "
                f"RTG positioning {self.rtg_positioning_s:g} s "
                f"(from session NBIRTH)"
            ),
            f"D-050 step 3d bound {self.bound_3d_s():.3f} s sim ({self.formula_3d()})",
            f"D-050 step 4 bound {self.bound_4_s():.3f} s sim ({self.formula_4()})",
        ]

    def as_dict(self, tt_name: str | None = None) -> dict:
        return {
            "profile": self.profile,
            "speed_mps": self.speed_mps,
            "qc_hold": list(self.qc_hold),
            "rtg_hold": list(self.rtg_hold),
            "qc_lane": [self.qc_lane.x, self.qc_lane.y],
            "block_tl": [self.block_tl.x, self.block_tl.y],
            "tt_starts": {name: [p.x, p.y] for name, p in self.tt_starts.items()},
            "time_scale": self.time_scale,
            "step_ms": self.step_ms,
            "rtg_positioning_s": self.rtg_positioning_s,
            "slack_s": self.slack_s,
            "bound_3d_s": round(self.bound_3d_s(tt_name), 3),
            "bound_4_s": round(self.bound_4_s(), 3),
            "formula_3d": self.formula_3d(tt_name),
            "formula_4": self.formula_4(),
            "tt_name": tt_name,
        }


def load_physical_bounds(
    repo_root: Path,
    *,
    nbirth: dict,
    time_scale: float,
    profile: str | None = None,
    speed_mps: float | None = None,
    env=None,
) -> PhysicalBounds:
    """Build D-050 bounds from the layout plus effective NBIRTH timings."""
    env = os.environ if env is None else env
    timings = timings_from_nbirth(nbirth)
    used_profile = profile or timings["profile"] or DEFAULT_TIMING_PROFILE
    if profile and timings["profile"] and timings["profile"] != profile:
        raise ValueError(
            f"requested timing profile {profile!r} does not match "
            f"NBIRTH {NBIRTH_PROFILE}={timings['profile']!r}"
        )
    if speed_mps is None:
        if env.get("SIMULATOR_DEFAULT_SPEED_MPS"):
            speed_mps = float(env["SIMULATOR_DEFAULT_SPEED_MPS"])
        else:
            speed_mps = parse_default_speed_mps(
                (repo_root / COMPOSE_REL).read_text(),
                (repo_root / HEADLESS_REL).read_text() if (repo_root / HEADLESS_REL).exists() else "",
            )
    layout_text = (repo_root / IMPORT_V1_REL).read_text()
    assert_layout_speed_is_default(layout_text)
    starts, qc_lane, block_tl = parse_import_v1_layout(layout_text)
    if timings["step_ms"] is not None:
        step_ms = timings["step_ms"]
    else:
        simclock = (
            (repo_root / SIMCLOCK_REL).read_text()
            if (repo_root / SIMCLOCK_REL).exists() else ""
        )
        step_ms = parse_fixed_step_ms(
            (repo_root / COMPOSE_REL).read_text() if (repo_root / COMPOSE_REL).exists() else "",
            simclock,
            env=env,
        )
    if timings["time_scale"] is not None:
        # NBIRTH is authoritative for the running session; keep the requested
        # scale only as a consistency check.
        if abs(timings["time_scale"] - time_scale) > 1e-9:
            raise ValueError(
                f"requested time_scale {time_scale:g} does not match "
                f"NBIRTH {NBIRTH_TIME_SCALE}={timings['time_scale']:g}"
            )
    return PhysicalBounds(
        profile=used_profile,
        speed_mps=speed_mps,
        qc_hold=timings["qc_hold"],
        rtg_hold=timings["rtg_hold"],
        qc_lane=qc_lane,
        block_tl=block_tl,
        tt_starts={name: pt for name, pt in starts.items() if name.startswith("TT")},
        time_scale=time_scale,
        step_ms=step_ms,
        rtg_positioning_s=timings["rtg_positioning_s"],
    )


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
    # D-037 clock: what started it, and the exact arrival time of the last
    # expected event (time.monotonic(), unrounded; the next step chains on it).
    clock_from: str = ""
    last_received_at: float | None = None
    # Simulator-clock occurred_at of the first/last matched sim event (S2).
    first_sim_at: datetime | None = None
    last_sim_at: datetime | None = None
    sim_elapsed_s: float | None = None
    # D-050: layout-derived sim bound and how it was computed (physical steps).
    sim_bound_s: float | None = None
    sim_bound_formula: str = ""
    # True when limit_s is the D-037 5 s wall-clock assertion (reaction steps).
    wall_asserted: bool = True
    # Step 4: RTG leg_started envelope stamp, used to keep dispatch latency
    # out of the sim-time bound (M2).
    rtg_leg_started_at: datetime | None = None
    # Wall-clock time from the step-4 dispatch request to the first
    # leg_dispatched; asserted against the 5 s reaction limit.
    dispatch_wall_s: float | None = None
    dispatch_limit_s: float | None = None

    def as_dict(self) -> dict:
        return {
            "step": self.step, "title": self.title, "trigger": self.trigger,
            "expected": self.expected, "observed": self.observed,
            "passed": self.passed, "elapsed_s": self.elapsed_s,
            "trigger_s": self.trigger_s, "limit_s": self.limit_s,
            "error": self.error, "checks": self.checks, "clock_from": self.clock_from,
            "sim_elapsed_s": self.sim_elapsed_s,
            "sim_bound_s": self.sim_bound_s,
            "sim_bound_formula": self.sim_bound_formula,
            "wall_asserted": self.wall_asserted,
            "dispatch_wall_s": self.dispatch_wall_s,
            "dispatch_limit_s": self.dispatch_limit_s,
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


def message_sim_datetime(message: UnsMessage) -> datetime | None:
    """Return data.occurred_at when it is a simulator-clock stamp, else None."""
    if message.event not in SIM_CLOCK_EVENTS:
        return None
    raw = message.data.get("occurred_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def record_sim_time(step: StepResult, message: UnsMessage) -> None:
    stamp = message_sim_datetime(message)
    if stamp is None:
        return
    if step.first_sim_at is None:
        step.first_sim_at = stamp
    step.last_sim_at = stamp
    che = str(message.data.get("che_name") or "")
    if message.event == "leg_started" and che.startswith("RTG"):
        step.rtg_leg_started_at = stamp


def annotate_sim_elapsed(results: list[StepResult]) -> None:
    """Fill sim_elapsed_s from envelope stamps for the step's physical work.

    Step 3d is measured from this step's first sim stamp (TT ``leg_started``,
    when the drive begins) to its last stamp. A short gap after the QC pick,
    while the TT DCMD is still in flight, is not layout work and is not
    counted (CI run 43977160552: pick at 26.900 s, TT start at 27.500 s,
    transfer at 56.000 s → drive 28.5 s, not 29.1 s).

    Step 4 is measured from the previous step's last stamp (H1 complete),
    because the remaining TT drive starts then, often before RTG
    ``leg_started``. Other simulator steps (3c) use first-to-last, or chain
    from the previous sim end when they have no earlier start. Steps with no
    sim-clock events (release, dispatch) are skipped and do not break the
    chain used by step 4.
    """
    prev_end = None
    for step in results:
        if step.last_sim_at is None:
            continue
        if step.step == "3d" and step.first_sim_at is not None:
            start = step.first_sim_at
        elif prev_end is not None:
            start = prev_end
        else:
            start = step.first_sim_at
        if start is not None:
            step.sim_elapsed_s = round((step.last_sim_at - start).total_seconds(), 3)
        prev_end = step.last_sim_at


def worst_step_summary(results: list[StepResult]) -> str | None:
    """Log line for the CI job summary: worst wall-clock step, plus its sim time."""
    timed = [step for step in results if step.elapsed_s is not None]
    if not timed:
        return None
    worst = max(timed, key=lambda step: step.elapsed_s)
    sim = (
        f"; sim time {worst.sim_elapsed_s:g} s"
        if worst.sim_elapsed_s is not None else ""
    )
    limit_note = (
        f"limit {worst.limit_s:g} s"
        if worst.wall_asserted else f"safety wait {worst.limit_s:g} s (not asserted)"
    )
    return (
        f"Worst step (wall-clock): {worst.step} {worst.title} "
        f"{worst.elapsed_s:g} s / {limit_note}{sim}."
    )


def _previous_sim_end(results: list[StepResult], current: StepResult) -> datetime | None:
    prev = None
    for step in results:
        if step is current:
            return prev
        if step.last_sim_at is not None:
            prev = step.last_sim_at
    return prev


def apply_dispatch_wall_assertion(step: StepResult) -> None:
    """Assert step 4's dispatch reaction against the 5 s wall-clock limit (M2)."""
    if step.dispatch_limit_s is None:
        return
    if step.error.startswith("skipped"):
        return
    if step.dispatch_wall_s is None:
        if step.passed or "timeout" not in step.error:
            # Events were matched but the dispatch stamp was not recorded.
            if any("leg_dispatched" in item for item in step.observed):
                step.passed = False
                step.error = (
                    "D-050: step 4 saw leg_dispatched but recorded no dispatch "
                    "wall-clock time"
                )
        return
    check = (
        f"D-050 dispatch reaction {step.dispatch_wall_s:g} s / "
        f"wall limit {step.dispatch_limit_s:g} s"
    )
    if step.dispatch_wall_s > step.dispatch_limit_s + 1e-9:
        step.passed = False
        step.error = f"dispatch reaction exceeds 5 s wall-clock limit: {check}"
    elif check not in step.checks:
        step.checks.append(check)


def apply_sim_time_assertions(
    results: list[StepResult],
    bounds: "PhysicalBounds | None" = None,
    tt_name: str | None = None,
) -> None:
    """Fail a physical-work step whose envelope sim span exceeds its layout bound."""
    annotate_sim_elapsed(results)
    for step in results:
        apply_dispatch_wall_assertion(step)
        if step.step not in PHYSICAL_STEPS:
            continue
        if step.error.startswith("skipped"):
            continue
        h1_end = _previous_sim_end(results, step) if step.step == "4" else None
        rtg_started = step.rtg_leg_started_at if step.step == "4" else None
        if bounds is not None:
            # Recompute step 4 once RTG leg_started is known, so a late
            # dispatch does not fail a layout bound (M2).
            step.sim_bound_s = round(
                bounds.sim_bound_s(
                    step.step, tt_name, h1_end=h1_end, rtg_started=rtg_started,
                ),
                3,
            )
            step.sim_bound_formula = bounds.formula(
                step.step, tt_name, h1_end=h1_end, rtg_started=rtg_started,
            )
        if step.sim_bound_s is None:
            continue
        if not step.passed and step.sim_elapsed_s is None:
            continue
        bound = step.sim_bound_s
        formula = step.sim_bound_formula or "layout-derived bound"
        if step.sim_elapsed_s is None:
            step.passed = False
            step.error = (
                f"D-050: no Event/TimeMs-derived occurred_at on simulator events, "
                f"cannot assert sim-time bound {bound:g} s ({formula})"
            )
            continue
        check = (
            f"D-050 sim time {step.sim_elapsed_s:g} s / bound {bound:g} s "
            f"({formula})"
        )
        if step.sim_elapsed_s > bound + 1e-9:
            step.passed = False
            step.error = f"sim time exceeds layout bound: {check}"
        elif check not in step.checks:
            step.checks.append(check)


def physical_steps_summary(results: list[StepResult]) -> str | None:
    parts = []
    for step in results:
        if step.step not in PHYSICAL_STEPS or step.sim_bound_s is None:
            continue
        sim = "—" if step.sim_elapsed_s is None else f"{step.sim_elapsed_s:g}"
        parts.append(f"{step.step} {sim} / {step.sim_bound_s:g} s")
    if not parts:
        return None
    return "Physical steps (D-050, sim time vs layout bound): " + "; ".join(parts) + "."


def accept_start_simulation(status: int, body, requested_time_scale: float) -> dict:
    """Accept HTTP 201, or HTTP 200 only when the reused session's scale matches."""
    payload = body if isinstance(body, dict) else {}
    if status == 201:
        if payload.get("id") is None:
            raise RuntimeError(f"start-simulation -> HTTP 201 but no session id: {body}")
        return payload
    if status == 200:
        if payload.get("id") is None:
            raise RuntimeError(f"start-simulation reused a session (HTTP 200) but no id: {body}")
        actual = payload.get("time_scale")
        try:
            matches = actual is not None and float(actual) == float(requested_time_scale)
        except (TypeError, ValueError):
            matches = False
        if not matches:
            raise RuntimeError(
                "start-simulation reused an existing session (HTTP 200) "
                f"with time_scale {actual}, requested {requested_time_scale:g}"
            )
        return payload
    raise RuntimeError(f"start-simulation -> HTTP {status}: {body}")


def session_setup_note(session: dict, *, created: bool) -> str:
    kind = "created" if created else "reused"
    return (
        f"Simulation session {session.get('id')} {kind} "
        f"(time_scale {session.get('time_scale')}, seed {session.get('seed')})"
    )


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
        record_sim_time(step, message)
        if (
            step.step == "4"
            and message.event == "leg_dispatched"
            and step.dispatch_wall_s is None
        ):
            step.dispatch_wall_s = round(message.received_at - start, 3)
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
        step.last_received_at = message.received_at
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


class BirthCollector:
    """Captures the session NBIRTH metrics from the broker (M1)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.metrics: dict | None = None
        self.topic: str | None = None
        self.error: str | None = None
        self.received = threading.Event()

    def accept(self, topic: str, payload: bytes) -> None:
        if "/NBIRTH/" not in str(topic):
            return
        try:
            metrics = metrics_by_name(decode_payload(payload))
            # Validate required holds now so a partial decode does not pass.
            timings_from_nbirth(metrics)
        except ValueError as exc:
            self.error = f"{topic}: {exc}"
            return
        with self._lock:
            self.metrics = metrics
            self.topic = topic
            self.error = None
        self.received.set()


def request_nbirth_rebirth(client, group_id: str, node_id: str) -> str:
    topic = f"spBv1.0/{group_id}/NCMD/{node_id}"
    client.publish(topic, encode_rebirth_ncmd(), qos=1)
    return topic


def start_mqtt(host: str, port: int, root: str, stream: EventStream, birth: BirthCollector | None = None):
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
            client.subscribe(SPARKPLUG_NBIRTH_FILTER, qos=1)

    def on_subscribe(*_args):
        connected.set()

    def on_message(_client, _userdata, msg):
        received = time.monotonic()
        if birth is not None and "/NBIRTH/" in str(msg.topic):
            birth.accept(msg.topic, bytes(msg.payload))
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
        raise RuntimeError(
            f"could not subscribe to {root}/# and {SPARKPLUG_NBIRTH_FILTER} "
            f"on {host}:{port}"
        )
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


def s1_plan(include_step4: bool, profile: str = DEFAULT_TIMING_PROFILE):
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
        ("3d", f"QC01 -> TT hand-off (Godot, {profile} timings)", "simulator", [
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
        self.birth = BirthCollector()
        self.nbirth_metrics: dict | None = None
        self.mqtt_client = None

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
        session = accept_start_simulation(status, body, a.time_scale)
        self.session_id = str(session["id"])
        created = status == 201

        def session_running():
            get_status, get_body = self.oes.request(
                "GET", f"/api/simulation-sessions/{self.session_id}/",
            )
            if get_body.get("actual_state") == "failed":
                raise SystemExit(f"simulation session failed: {get_body.get('failure_reason')}")
            return get_status == 200 and get_body.get("actual_state") == "running"

        wait_until(session_running, a.setup_timeout, 2, "the simulation session to be running (is the runner up?)")
        # Seed and timing params are filled from NBIRTH once the session is running.
        get_status, got = self.oes.request("GET", f"/api/simulation-sessions/{self.session_id}/")
        if get_status == 200 and isinstance(got, dict):
            session = got
        self.setup_notes.append(session_setup_note(session, created=created))
        self.ensure_nbirth(session, created=created)

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

    def ensure_nbirth(self, session: dict, *, created: bool) -> None:
        """Wait for the session NBIRTH; request a Rebirth if we missed it."""
        if self.nbirth_metrics:
            return
        if self.birth.received.is_set():
            self._apply_nbirth(session)
            return
        # A reused (HTTP 200) session was already born before we subscribed.
        group = session.get("sparkplug_group_id")
        node = session.get("sparkplug_node_id")
        if self.mqtt_client is not None and group and node:
            topic = request_nbirth_rebirth(self.mqtt_client, str(group), str(node))
            self.setup_notes.append(
                f"NBIRTH not yet seen (session {'created' if created else 'reused'}); "
                f"sent Node Control/Rebirth on {topic}"
            )
        if not self.birth.received.wait(min(30.0, getattr(self.args, "setup_timeout", 180.0))):
            detail = self.birth.error or "no NBIRTH received"
            raise TimeoutError(
                "setup timeout waiting for session NBIRTH "
                f"(Sim/Timing/QcHold* and RtgHold*): {detail}. "
                "The D-050 bounds read effective timings from NBIRTH; "
                "start a fresh simulation session and retry."
            )
        self._apply_nbirth(session)

    def _apply_nbirth(self, session: dict) -> None:
        metrics = self.birth.metrics
        if not metrics:
            raise RuntimeError("NBIRTH collector was signalled but has no metrics")
        self.nbirth_metrics = metrics
        timings = timings_from_nbirth(metrics)
        seed = metrics.get("Sim/Seed", session.get("seed"))
        self.setup_notes.append(
            f"Session NBIRTH on {self.birth.topic}: profile {timings['profile']}, "
            f"QC hold {timings['qc_hold'][0]:g}..{timings['qc_hold'][1]:g} s, "
            f"RTG hold {timings['rtg_hold'][0]:g}..{timings['rtg_hold'][1]:g} s, "
            f"RTG positioning {timings['rtg_positioning_s']:g} s, "
            f"tick {timings['step_ms'] or 'unset'} ms, seed {seed}"
        )
        overrides = hold_override_env_keys()
        if overrides:
            self.setup_notes.append(
                "Hold env overrides are set ("
                + ", ".join(overrides)
                + "); bounds use the effective NBIRTH holds"
            )

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
            step.error = (
                f"release command failed (exit {code}): {output[-500:]} "
                "(precondition: scenario-reset, then scenario-step visit-in-operation)"
            )
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

    def load_bounds(self) -> PhysicalBounds:
        if getattr(self, "bounds", None) is None:
            if not self.nbirth_metrics:
                raise RuntimeError(
                    "D-050 bounds need the session NBIRTH timings; none were received"
                )
            profile = getattr(
                self.args, "timing_profile", None,
            ) or os.environ.get("SIMULATOR_TIMING_PROFILE", DEFAULT_TIMING_PROFILE)
            time_scale = float(getattr(self.args, "time_scale", 10.0))
            self.bounds = load_physical_bounds(
                REPO_ROOT,
                nbirth=self.nbirth_metrics,
                profile=profile,
                time_scale=time_scale,
            )
            self.setup_notes.extend(self.bounds.setup_notes())
        return self.bounds

    def run_steps(self) -> None:
        """Run the plan with the D-037 / D-050 clocks.

        A step's clock starts when its trigger is published: just before the
        MiniTOS release command or the OES ``dispatch-next`` request is issued,
        or, for simulator-driven steps, when the previous step's last expected
        event was received from the broker (the UNS publication; envelope
        times of simulator events are sim time, not wall time). It stops when
        the step's last expected event is received. Reaction-only steps are
        asserted against the 5 s wall-clock limit; physical-work steps (3d, 4)
        wait only up to a generous safety timeout and are asserted in sim time.
        Step 4's dispatch itself is still checked against the 5 s wall limit.
        """
        bounds = self.load_bounds()
        previous: StepResult | None = None
        for step_id, title, trigger, expectations in s1_plan(
            self.args.include_step4, profile=bounds.profile,
        ):
            physical = step_id in PHYSICAL_STEPS
            tt_name = getattr(self, "tt_name", None)
            if physical:
                limit_s = bounds.safety_wall_s(step_id, tt_name)
                sim_bound_s = round(bounds.sim_bound_s(step_id, tt_name), 3)
                formula = bounds.formula(step_id, tt_name)
            else:
                limit_s = self.args.step_limit
                sim_bound_s = None
                formula = ""
            step = StepResult(
                step=step_id, title=title, trigger=trigger,
                expected=[e.label() for e in expectations], limit_s=limit_s,
                sim_bound_s=sim_bound_s, sim_bound_formula=formula,
                wall_asserted=not physical,
                dispatch_limit_s=self.args.step_limit if step_id == "4" else None,
            )
            self.results.append(step)
            if any(not r.passed for r in self.results[:-1]):
                step.error = "skipped: an earlier step failed"
                continue
            if trigger == "minitos-release":
                step.clock_from = "release command issued"
                start = time.monotonic()
                if not self.trigger_release(step):
                    continue
            elif trigger == "oes-dispatch":
                if self.oes_order_id is None:
                    self.oes_order_id = self.find_oes_order()
                step.clock_from = "dispatch-next request sent"
                start = time.monotonic()
                if not self.trigger_dispatch(step):
                    continue
                if step_id == "3b":
                    # AC3.2 right away, while H1 is surely still in flight.
                    self.check_redispatch_noop(step)
                    if step.error:
                        continue
            else:
                if previous is None or previous.last_received_at is None:
                    step.error = "no triggering event: a simulator-driven step must follow a passed step"
                    continue
                step.clock_from = f"{previous.observed[-1]} received (end of step {previous.step})"
                start = previous.last_received_at
            run_expectations(step, self.stream, expectations, start, self.on_message)
            previous = step
        apply_sim_time_assertions(self.results, bounds, getattr(self, "tt_name", None))
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

def _wall_limit_cell(step: StepResult) -> str:
    if step.wall_asserted:
        return f"{step.limit_s:g}"
    return f"safety {step.limit_s:g}"


def render_markdown(results: list[StepResult], notes: list[str], fatal: str = "") -> str:
    annotate_sim_elapsed(results)
    lines = ["## TCL-007 S1 slice (import_v1)", ""]
    if fatal:
        lines += [f"**SETUP FAILED:** {fatal}", ""]
    for note in notes:
        lines.append(f"- {note}")
    lines += [
        "",
        "Wall time = from the trigger's publication (Clock from) to receipt of the step's last expected event. "
        "Reaction-only steps (3a, 3b, 3c) are asserted against the 5 s wall-clock limit (D-037). "
        "Steps 3d and 4 are asserted in sim time from data.occurred_at (Event/TimeMs envelope stamps) "
        "against a layout-derived bound (D-050); their wall time is logged and the wait uses only a safety timeout. "
        "Trigger = how long the trigger call itself took (included in Wall).",
        "",
        "| Step | Result | Wall (s) | Wall limit (s) | Sim (s) | Sim bound (s) | Clock from | Trigger (s) | Expected | Observed | Detail |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        verdict = "PASS" if r.passed else ("SKIP" if r.error.startswith("skipped") else "FAIL")
        detail = "; ".join([*r.checks, r.error] if r.error else r.checks).replace("|", "\\|")
        sim = "" if r.sim_elapsed_s is None else r.sim_elapsed_s
        bound = "" if r.sim_bound_s is None else r.sim_bound_s
        lines.append(
            f"| {r.step} {r.title} | {verdict} | {'' if r.elapsed_s is None else r.elapsed_s} | "
            f"{_wall_limit_cell(r)} | {sim} | {bound} | {r.clock_from} | "
            f"{'' if r.trigger_s is None else r.trigger_s} | "
            f"{'<br>'.join(r.expected)} | {'<br>'.join(r.observed)} | {detail} |"
        )
    passed = bool(results) and not fatal and all(r.passed for r in results)
    lines += ["", f"**Overall: {'PASS' if passed else 'FAIL'}**", ""]
    worst = worst_step_summary(results)
    if worst:
        lines += [worst, ""]
    physical = physical_steps_summary(results)
    if physical:
        lines += [physical, ""]
    return "\n".join(lines)


def write_reports(args, results, notes, stream: EventStream | None, fatal="", bounds=None, tt_name=None) -> bool:
    apply_sim_time_assertions(results, bounds, tt_name)
    passed = bool(results) and not fatal and all(r.passed for r in results)
    markdown = render_markdown(results, notes, fatal)
    print(markdown)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "s1_slice.md").write_text(markdown)
    payload = {
        "passed": passed, "fatal": fatal, "notes": notes,
        "steps": [r.as_dict() for r in results],
    }
    if bounds is not None:
        payload["d050_bounds"] = bounds.as_dict(tt_name)
    (report_dir / "s1_slice.json").write_text(json.dumps(payload, indent=2))
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
                   help="D-037/D-050: 5 s wall-clock limit for reaction-only steps (3a, 3b, 3c). Do not raise to hide flakiness; open a ticket.")
    p.add_argument("--time-scale", type=float, default=float(os.environ.get("E2E_TIME_SCALE", "10")),
                   help="Simulation session time_scale (CI: 10, because import_v1 TT drives are 20-32 sim-s; QC/RTG holds add <= 5 sim-s and are not the binding wait). D-050 keeps this scale and asserts 3d/4 in sim time.")
    p.add_argument("--timing-profile", default=os.environ.get("SIMULATOR_TIMING_PROFILE", DEFAULT_TIMING_PROFILE),
                   help="Expected SIMULATOR_TIMING_PROFILE; must match NBIRTH Sim/Timing/Profile (CI: fast).")
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
        run.mqtt_client = start_mqtt(
            args.mqtt_host, args.mqtt_port, args.uns_root, run.stream, birth=run.birth,
        )
        client = run.mqtt_client
        run.setup_notes.append(
            f"Subscribed to {args.uns_root}/# and {SPARKPLUG_NBIRTH_FILTER} "
            f"on {args.mqtt_host}:{args.mqtt_port}"
        )
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
    return 0 if write_reports(
        args, run.results, run.setup_notes, run.stream, fatal,
        bounds=getattr(run, "bounds", None), tt_name=getattr(run, "tt_name", None),
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
