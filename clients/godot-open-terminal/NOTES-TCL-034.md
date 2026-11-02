# TCL-034: simulation timing parameters, no-show time-outs and leg stall clock

Implements the Godot side of the TCL-011 hand-off design (§6.1 to §6.3) and D-027
(every CHE has the same failure logic; the time-out is a user parameter at
simulation initialisation).

## Parameters

Precedence: environment variable > timing profile > built-in default.

| Environment variable | NBIRTH metric | Default |
|---|---|---|
| `SIMULATOR_TIMING_PROFILE` | `Sim/Timing/Profile` (String) | `fast` |
| `SIMULATOR_TT_NO_SHOW_TIMEOUT_S` | `Sim/Timing/TtNoShowTimeoutS` | 300 |
| `SIMULATOR_QC_NO_SHOW_TIMEOUT_S` | `Sim/Timing/QcNoShowTimeoutS` | 300 |
| `SIMULATOR_RTG_NO_SHOW_TIMEOUT_S` | `Sim/Timing/RtgNoShowTimeoutS` | 300 |
| `SIMULATOR_LEG_STALL_TIMEOUT_S` | `Sim/Timing/LegStallTimeoutS` | 600 |

The `SIMULATOR_{QC,RTG}_WAIT_MIN_S/_MAX_S` and `..._HOLD_WAIT_MIN_S/_MAX_S` keys
still apply and win over the profile. `SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED` and
`Sim/Timing/RtgNoShowTimeoutEnabled` are retired: the RTG side is always on.

The names are **pending BE confirmation**. The NBIRTH `ContractVersion` stays
`tcl-002.2` until BE fixes the revision (rev 9.4 assumed).

## Profiles (assumptions, pending E3/Q22)

* `fast` (default): QC and RTG holds of 2 to 5 s. Every fast step is at most 5 s.
* `realistic`: QC hold of 90 to 120 s, RTG hold of 60 to 120 s (D-039). Only the
  holds change. Drive times still come from distance and speed.

## Waits and time-outs

| Wait | Who waits | For | Time-out | Reported on |
|---|---|---|---|---|
| W1 | QC | TT | TtNoShow | QC leg |
| W2 | TT at the QC lane | QC | QcNoShow | TT leg |
| W3 | TT at the block lane (H2) | RTG | RtgNoShow | TT `assign_handoff` command (assumption) |
| W4 | RTG | TT | TtNoShow (always on) | RTG leg |

Each fires once per hand-off as `handoff_timed_out` (`SIMULATOR_FAILURE`), with
`handoff_role` and the counterpart. After that, the waiting CHE no longer
completes that HandoffId.

Leg stall clock: a leg with no progress result for `LegStallTimeoutS` of sim
time is reported as `failed`, reason `SIMULATOR_FAILURE`, detail
`no progress on <CHE> within N s`. The CHE is then idle and free, because OES
treats `failed` as terminal.

## Margin check

`SimTiming.margin_violations()` requires each no-show time-out to be at least 2×
the worst normal wait, and the stall to be at least 2× the longest gap. Worst
normal waits: W1 = RTG hold max (one TT cycle, about 60 to 120 s), W2 = QC hold
max, W3 = RTG hold max, W4 = the TT drive over `DEMO_MAX_TT_DRIVE_M` = 200 m
(assumption, E22 open). The defaults pass for both profiles (unit test). Overrides
that break the margin are logged as warnings and are not rejected.

## Open points (for BE / TL)

* BE: OES accepts `handoff_timed_out` from the TT (W2/W3) and the RTG (W4), and
  handles `failed` + `SIMULATOR_FAILURE` from the stall clock (§6.3). Unconfirmed.
* BE: the W3 report is sent on the TT `assign_handoff` command id. Unconfirmed.
* TL: S2 dispatch is manual. A TT waiting at the block lane for a manual H2
  dispatch for more than 600 s will hit the stall clock.
* QA: the default holds are now 2 to 5 s (`fast`), no longer 10 to 30 s. Survey
  cases that assume [10, 30] need `SIMULATOR_TIMING_PROFILE` or explicit keys.

## Tests

`tests/unit/test_headless_sim.gd` covers both profiles, precedence (env >
profile > default), determinism per profile, fast steps ≤ 5 s, margins for both
profiles, W1 to W4, and the stall clock. `apps/simulator-runner/test_runner.py`
and `apps/terminal-oes/execution/test_handoffs.py` check both ways that the keys
Godot reads, the OES settings list and the runner allowlist match.
