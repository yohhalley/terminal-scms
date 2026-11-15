# TCL-034e: Godot timing decisions D-051, D-052 and D-053

Builds on `NOTES-TCL-034.md`. Where the two disagree, this file wins.
Yael's answers of 15 Nov 2026 are recorded in the team decision log (D-051 to D-053).
Product questions still open with Yael are labelled Y1 to Y5, and backend points A1 to A4.

## D-051: realistic timings are the full crane cycle

* `SimTiming.gd` stores a **cycle** for the realistic profile: `REALISTIC_QC_CYCLE_S` and `REALISTIC_RTG_CYCLE_S`, both `[60, 60]` (inclusive whole seconds, min and max).
* The hold is `max(0, cycle - simulated motion)`, using `QC_SIMULATED_MOTION_S` and `RTG_SIMULATED_MOTION_S`.
* No crane motion is simulated today:
  * QC: the box appears when the command arrives (D-012), and the deposit is instant.
  * RTG: `gantry_to_bay`, the pick and the drop all take 0 s.
  * So the hold equals the cycle, 60 s.
* For the RTG, the whole cycle sits **after the pick**.
* `fast` keeps its 2 to 5 s holds as they are.
* The exact range is **pending Yael (Y1)**. To change it, edit only the two cycle constants. If gantry motion is later modelled before the pick (Y2), raise `RTG_SIMULATED_MOTION_S` and call `_enter_ready_to_pick` later.
* The NBIRTH fields `Sim/Timing/{Qc,Rtg}Hold{Min,Max}S` still report the **effective hold**.
* Overrides still apply: `SIMULATOR_{QC,RTG}_[HOLD_]WAIT_MIN/MAX_S` win over the profile.
* Margins with the 60 s cycle:
  * W1, W2 and W3: 60 s worst case.
  * W4: the 50 s TT drive.
  * The defaults (300 s no-show, 600 s stall) pass with room to spare.
* With a fixed cycle, the seed no longer changes the realistic holds. `fast` runs still vary with the seed.

## D-052: W4 starts when the RTG is at the bay and ready to pick

* `RtgStack._enter_ready_to_pick` records `ready_to_pick_at_ms` when the RTG leaves `gantry_to_bay`.
* If a TT is ready on that step (same HandoffId, TT is the giver, at `at_destination_waiting`, hand-off not closed, counterparts match), the RTG picks at once and **no W4 opens**.
* Otherwise the RTG enters `waiting_for_tt` with `waiting_since_ms = ready_to_pick_at_ms`.
* W4 never starts on the RTG command, on the TT's arrival or on the H2 assign.
* With 0 s of gantry travel, ready-to-pick is the first step after the command, so runs behave as before.
* The W4 margin still uses the TT drive, because the RTG can be ready before the loaded TT arrives.

## D-053: one no-show switch, one clock per waiting period

| Environment variable | NBIRTH metric | Default |
|---|---|---|
| `SIMULATOR_NO_SHOW_TIMEOUT_ENABLED` (local alias `SIM_NO_SHOW_TIMEOUT_ENABLED`) | `Sim/Timing/NoShowTimeoutEnabled` (Boolean) | `true` |

* One **global** switch covers W1 to W4 (whether to split it per CHE type is question Y3).
* Accepted values: `true/false`, `1/0`, `yes/no` and `on/off`, case-insensitive. Anything else keeps the current value and logs a warning. The profile does not set the switch.
* `Contract/Version` is now **`tcl-002.3`**.
* The switch is checked in `_no_show_due_for`. That is the only path that publishes `handoff_timed_out`, because the crane state machines run with `emit_no_show = false`.
* When the switch is **off**:
  * no CHE ever publishes `handoff_timed_out`;
  * waits still open and still publish `waiting_for_handoff` and `Work/State`;
  * the no-show margin rows are skipped, and the stall row is still checked.
* The **leg stall clock does not change** when the switch is off. It stays paused in every wait state, so it is **not** a backstop. With the switch off, a waiter whose counterpart never comes waits until OES cancels (question Y5).
* `SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED` stays retired. Setting it only logs a warning that points to the new key. It is not an alias.

### Waiting periods

A waiting period is one continuous stay of one waiting CHE in a wait state, for one HandoffId. During it the order is active but the CHE cannot finish its job. Each period starts its own clock. Clocks never add up across a leg or a CHE.

| Wait | Waiter | Clock starts | Ends |
|---|---|---|---|
| W1 | QC, box ready | the hold ends with no TT (`HOLDING → WAITING_FOR_TT`) | H1, time-out or cancel |
| W2 | TT at the QC lane (H1 receiver) | the TT arrives at the source (`_start_tt_wait_period`) | H1 (clock cleared), time-out or cancel |
| W3 | TT at the block lane (H2 giver) | the TT arrives holding H2, **or** the H2 assign reaches a TT already waiting | H2, time-out or cancel |
| W4 | RTG at the bay (H2 receiver) | ready-to-pick with no TT (D-052) | H2, time-out or cancel |
| (none) | TT at the block lane without H2 | no clock: this is waiting on OES during manual dispatch | the H2 assign starts W3 |

* At most one `handoff_timed_out` is published per HandoffId (giver first, TCL-011 S1).
* After a time-out the waiter does **not** re-arm, even if it keeps waiting (question Y4).

## Tests (`tests/unit/test_headless_sim.gd`)

**Changed**
* The profile and precedence tests and the env precedence test now use the cycle-derived holds.
* `_test_realistic_hold_is_not_a_w2_no_show`.
* `_test_profile_determinism`: a fixed cycle draws no randomness, so the seed check now runs on `fast`.
* The tight-margin case.
* `_test_configurable_qc_waits` defaults.
* `_test_no_show_switch_default_on`, which replaces `_test_rtg_no_show_always_on`.
* `_test_retired_rtg_key_only_warns`.
* `_test_node_birth_metrics`: checks `tcl-002.3` and the Boolean field.

**New**
* `_test_hold_derived_from_cycle`
* `_test_w4_starts_at_ready_to_pick`
* `_test_no_show_switch_parsing`
* `_test_no_show_switch_off_all_waits`: W1 to W4 at 10 × the time-out produce no report and no stall
* `_test_no_show_clock_restarts_per_period`
* `_test_no_rearm_after_timeout`
* `_test_margins_skip_no_show_when_off`

`fast`-profile traces are byte-identical before and after this change. The seeds checked were 1, 7, 42 and 2024, across the single-leg, chain and profile traces.

## Outside the Godot client (BE asked for this; ownership is question A3)

* `apps/simulator-runner/runner.py`: `ALLOWED_ENVIRONMENT`.
* `apps/terminal-oes/terminal_oes/settings.py`: `SIMULATOR_SIM_SETTING_NAMES`.
* The cross-check tests `apps/simulator-runner/test_runner.py` and `apps/terminal-oes/execution/test_handoffs.py`.
* The docs `apps/simulator-runner/README.md` and `apps/terminal-oes/docs/UNS_CONTRACT.md`.
* Not done here, left with BE (A1): OES storing `NoShowTimeoutEnabled` from NBIRTH, and `compose.yaml` / `.env.example` passing the key through.
