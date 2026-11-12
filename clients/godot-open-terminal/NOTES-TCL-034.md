# TCL-034: simulation timing parameters, no-show time-outs and leg stall clock

Implements the Godot side of the TCL-011 hand-off design (§6.1 to §6.3) and D-027
(every CHE has the same failure logic; the time-out is a user parameter at
simulation initialisation).

## Parameters

Precedence: environment variable > timing profile > built-in default.

| Environment variable | NBIRTH metric | Default |
|---|---|---|
| `SIMULATOR_TIMING_PROFILE` | `Sim/Timing/Profile` (String) | `realistic` |
| `SIMULATOR_TT_NO_SHOW_TIMEOUT_S` | `Sim/Timing/TtNoShowTimeoutS` | 300 |
| `SIMULATOR_QC_NO_SHOW_TIMEOUT_S` | `Sim/Timing/QcNoShowTimeoutS` | 300 |
| `SIMULATOR_RTG_NO_SHOW_TIMEOUT_S` | `Sim/Timing/RtgNoShowTimeoutS` | 300 |
| `SIMULATOR_LEG_STALL_TIMEOUT_S` | `Sim/Timing/LegStallTimeoutS` | 600 |

The `SIMULATOR_{QC,RTG}_WAIT_MIN_S/_MAX_S` and `..._HOLD_WAIT_MIN_S/_MAX_S` keys
still apply and win over the profile. `SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED` and
`Sim/Timing/RtgNoShowTimeoutEnabled` are retired: the RTG side is always on.

The names are **pending BE confirmation**. The NBIRTH `ContractVersion` stays
`tcl-002.2` until BE fixes the revision (rev 9.4 assumed).

## Profiles (values pending E3/Q22)

* `realistic` (default, D-039: the testing server is the demo): QC hold of 90
  to 120 s, RTG hold of 60 to 120 s. Only the holds change. Drive times still
  come from distance and speed.
* `fast`: QC and RTG holds of 2 to 5 s. Every fast step is at most 5 s. CI, the
  Godot test harness (`.github/scripts/run-godot-test.sh`), the CI workflow env
  (`godot` and `godot-mqtt-broker` jobs, with a check step) and the runner tests
  set `SIMULATOR_TIMING_PROFILE=fast` explicitly; `test_headless_sim.gd` fails
  if the harness does not pass it.
* `compose.yaml` / `.env.example` pass `SIMULATOR_TIMING_PROFILE` (default
  `realistic`) to OES, which forwards it in the claim environment.

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

Leg stall clock (TCL-011 rev 2, B1): it measures unexplained silence only. It
runs only while the leg is in a motion or work state (driving, gantrying,
holding, picking, dropping), restarts on every progress result, and is
**paused** in `STALL_PAUSED_STATES`:

* hand-off waits with their own no-show clock: TT `at_source_waiting` (W2), TT
  `at_destination_waiting` with H2 (W3), QC / RTG `waiting_for_tt` (W1, W4),
  including after the no-show fired while OES has not cancelled yet;
* waiting on OES: the TT in `at_destination_waiting` with no hand-off (the S2
  manual-dispatch gap before `dispatch-next` #2), and finished legs.

After `LegStallTimeoutS` of running sim time the leg is reported as `failed`,
reason `SIMULATOR_FAILURE`, detail `no progress on <CHE> within N s`. The CHE
is then idle and free, because OES treats `failed` as terminal.

The retired `SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED` only triggers a deprecation
warning (louder when it is off); it is no longer allowlisted.

## Margin check

`SimTiming.margin_violations()` requires each no-show time-out to be at least 2×
the worst normal wait, and the stall to be at least 2× the longest single
motion/work phase (a crane hold or the TT drive; N2). No-show waits no longer
count for the stall, since the stall clock is paused there. Worst
normal waits: W1 = RTG hold max (one TT cycle, about 60 to 120 s), W2 = QC hold
max, W3 = RTG hold max, W4 = the TT drive over `DEMO_MAX_TT_DRIVE_M` = 200 m
(assumption, pending E22). The defaults pass for both profiles (unit test). Overrides
that break the margin are logged as warnings and are not rejected.

## Open points (for BE / TL)

* BE: OES accepts `handoff_timed_out` from the TT (W2/W3) and the RTG (W4), and
  handles `failed` + `SIMULATOR_FAILURE` from the stall clock (§6.3). Unconfirmed.
* BE: the W3 report is sent on the TT `assign_handoff` command id. Unconfirmed.
* Same-tick giver-first rule (TCL-011 rev 2, S1): if the giver and receiver
  no-show clocks expire in the same tick, Godot publishes only the giver's
  `handoff_timed_out` and closes the hand-off locally. The receiver publishes
  only if its clock expired and the giver's did not. One report per HandoffId,
  independent of CHE iteration order. Covered by a W3+W4 regression test.
* QA: the built-in holds are now `realistic` (QC 90 to 120 s, RTG 60 to 120 s);
  tests and CI run `fast` (2 to 5 s). The old 10 to 30 s holds are gone. Survey
  cases that assume [10, 30] need explicit hold keys.

## Tests

`tests/unit/test_headless_sim.gd` covers both profiles, precedence (env >
profile > default), determinism per profile, fast steps ≤ 5 s, margins for both
profiles, W1 to W4, giver-first same-tick publication of W3+W4 (including
reversed CHE iteration order), and the stall clock: a manual H2 dispatch wait of
more than 600 s (and of 10 × the clock) raises nothing, a stall while driving or
while holding fails after 600 s, and the clock stays paused in W1 to W4 even
when the no-show time-out is longer than the stall clock.
`apps/simulator-runner/test_runner.py` and
`apps/terminal-oes/execution/test_handoffs.py` check both ways that the keys
Godot reads, the OES settings list and the runner allowlist match.
