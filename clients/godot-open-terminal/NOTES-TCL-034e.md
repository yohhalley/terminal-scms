# TCL-034e: Godot timing decisions D-051 to D-060

Builds on `NOTES-TCL-034.md`. Where the two disagree, this file wins.
Yael's answers are recorded in the team decision log: D-051 to D-053 (15 Nov 2026),
then D-054 (Y1: 60 s for QC and RTG), D-055 (Y3: one global switch), D-058 (Y2),
D-059 (Y4) and D-060 (Y5). Backend points are labelled A1 to A4.

## D-051: realistic timings are the full crane cycle

* `SimTiming.gd` stores a **cycle** for the realistic profile: `REALISTIC_QC_CYCLE_S` and `REALISTIC_RTG_CYCLE_S`, both `[60, 60]` (inclusive whole seconds, min and max).
* The hold is `max(0, cycle - simulated motion)`, using `QC_SIMULATED_MOTION_S` and `RTG_SIMULATED_MOTION_S`.
* Simulated motion:
  * QC: none. The box appears when the command arrives (D-012), and the deposit is instant. So the QC hold equals the cycle, 60 s.
  * RTG: the D-058 positioning, `RTG_SIMULATED_MOTION_S = REALISTIC_RTG_POSITIONING_S = 30`. So the RTG hold after the pick is 60 − 30 = 30 s. The pick and the drop take 0 s.
* `fast` keeps its 2 to 5 s holds as they are, with 0 s RTG positioning.
* D-054 fixed the cycle at 60 s for both. To change it, edit only the cycle constants.
* The NBIRTH fields `Sim/Timing/{Qc,Rtg}Hold{Min,Max}S` still report the **effective hold**.
* Overrides still apply: `SIMULATOR_{QC,RTG}_[HOLD_]WAIT_MIN/MAX_S` win over the profile.
* Margins with the 60 s cycle:
  * W1 and W3: one RTG cycle (positioning + hold), 60 s. W2: the QC hold, 60 s.
  * W4: the 50 s TT drive.
  * The defaults (300 s no-show, 600 s stall) pass with room to spare.
* With a fixed cycle, the seed no longer changes the realistic holds. `fast` runs still vary with the seed.

## D-052: W4 starts when the RTG is at the bay and ready to pick

* `RtgStack._enter_ready_to_pick` records `ready_to_pick_at_ms` when the RTG leaves `gantry_to_bay`.
* If a TT is ready on that step (same HandoffId, TT is the giver, at `at_destination_waiting`, hand-off not closed, counterparts match), the RTG picks at once and **no W4 opens**.
* Otherwise the RTG enters `waiting_for_tt` with `waiting_since_ms = ready_to_pick_at_ms`.
* W4 never starts on the RTG command, on the TT's arrival or on the H2 assign.
* With 0 s positioning (`fast`), ready-to-pick is the first step after the command, so fast runs behave as before. With D-058 positioning it is the first step at or after command + `RtgPositioningS`.
* The W4 margin still uses the TT drive, because the RTG can be ready before the loaded TT arrives.

## D-053: one no-show switch, one clock per waiting period

| Environment variable | NBIRTH metric | Default |
|---|---|---|
| `SIMULATOR_NO_SHOW_TIMEOUT_ENABLED` (local alias `SIM_NO_SHOW_TIMEOUT_ENABLED`) | `Sim/Timing/NoShowTimeoutEnabled` (Boolean) | `true` |

* One **global** switch covers W1 to W4 (D-055 confirmed one switch, not one per CHE type).
* Accepted values: `true/false`, `1/0`, `yes/no` and `on/off`, case-insensitive. Anything else keeps the current value and logs a warning. The profile does not set the switch.
* `Contract/Version` was **`tcl-002.3`** for this switch, and is now **`tcl-002.4`** (D-058, D-059).
* The switch is checked in `_no_show_due_for`. That is the only path that publishes `handoff_timed_out`, because the crane state machines run with `emit_no_show = false`.
* When the switch is **off**:
  * no CHE ever publishes `handoff_timed_out`;
  * waits still open and still publish `waiting_for_handoff` and `Work/State`;
  * the no-show margin rows are skipped, and the stall row is checked against the worst wait as well (D-060);
  * the leg stall clock is the backstop (D-060, below).
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

* One CHE reports per HandoffId (giver first on a same-tick tie, TCL-011 S1). D-059 lets that CHE repeat its report (below); its counterpart never reports.

## D-058: the RTG positions ~30 s before the truck arrives

Yael: "Yard crane: it positions for ~30 s before the truck arrives; the no-show clock starts after that."

Interpretation chosen (the simplest one that fits D-051, D-052 and D-054):
* The realistic 60 s RTG cycle is split: **30 s positioning** before the pick + **30 s hold** after it.
* Positioning is the existing `gantry_to_bay` state. It starts at the RTG command (`RtgStack.start(..., positioning_ms, now_ms)` sets `positioned_at_ms`). It does not depend on where the TT is.
* While positioning, the RTG cannot pick, even if the TT is already at the lane. That TT is in W3 (its own clock, `RtgNoShowTimeoutS`).
* When positioning ends the RTG is "at the bay and ready to pick", the D-052 moment. W4 (the RTG's no-show clock) starts there if no TT is ready, so W4 starts after positioning, as Yael said.
* `SimTiming.REALISTIC_RTG_POSITIONING_S = 30`, and the profile key `rtg_positioning_s` (`fast` 0). `RTG_SIMULATED_MOTION_S` = the positioning, so D-051's "hold = cycle − simulated motion" gives 30 s.
* NBIRTH `Sim/Timing/RtgPositioningS` (Int64). There is no env override (no new runner or OES allowlist key).
* `fast`: 0 s positioning, so fast traces are unchanged.

## D-059: report again every period while still waiting

Yael: "Report again every 5 minutes while it keeps waiting."

* The CHE that reported `handoff_timed_out` reports it again each time its own period passes since its last report. The period is the same N as the first report (`TtNoShowTimeoutS` for QC and RTG, `QcNoShowTimeoutS` / `RtgNoShowTimeoutS` for the TT; default 300 s = 5 minutes).
* It repeats only while it is still in the same wait for that HandoffId and its leg is not cancelled. The OES cancel stops it.
* The hand-off stays closed locally: a late counterpart still gets no transfer. The counterpart never reports.
* Gated by the D-053 switch. Each repeat has its own `Command/Result/Id`, on the same command id (the TT's H2 reports stay on the assign id).
* State lives in the command (`no_show_reported_at_ms`, `no_show_repeat_ms`, `no_show_report_count`, `no_show_phase`), so it survives a restart.
* `Contract/Version` `tcl-002.4`.
* OES today (no change made here): a repeat on an `exception` or `cancelled` hand-off is logged as an "idempotent no-op" and stored as an INFORMATIONAL receipt. It raises no second `handoff_exception` (that event also has a derived id per hand-off), and it sends no second cancel or block routing. If the first report never reached OES (the hand-off is still `pending`), the repeat raises the exception. A repeat after `transferred` would be rejected and flagged, but Godot does not send one.

## D-060: with the switch off, the stall clock is the backstop

Yael: "With the switch off, the 10-minute stuck-job check still ends the job as a backup."

* With `NoShowTimeoutEnabled` false, the leg stall clock is **not** paused in open hand-off waits: QC or RTG in `waiting_for_tt` (W1, W4), or a TT at the lane with its H1/H2 wait started (W2, W3).
* After `LegStallTimeoutS` (default 600 s) with no result on the leg, the leg gets `failed` with `SIMULATOR_FAILURE` and "no progress on <CHE> within N s". The clock counts from the leg's last result (`waiting_for_handoff` at the start of the wait).
* That closes the hand-off, so its counterpart's stall clock pauses again and it waits for the OES cancel. There is one failure per hand-off.
* Waits on OES (the TT at the block without H2, finished legs) still pause the clock.
* With the switch on nothing changes: waits pause the stall clock, and the no-show clock owns them.

## Tests (`tests/unit/test_headless_sim.gd`)

**Changed**
* The profile and precedence tests and the env precedence test now use the cycle-derived holds.
* `_test_realistic_hold_is_not_a_w2_no_show`.
* `_test_profile_determinism`: a fixed cycle draws no randomness, so the seed check now runs on `fast`.
* The tight-margin case.
* `_test_configurable_qc_waits` defaults.
* `_test_no_show_switch_default_on`, which replaces `_test_rtg_no_show_always_on`.
* `_test_retired_rtg_key_only_warns`.
* `_test_node_birth_metrics`: checks `tcl-002.4` and the Boolean field.
* `_test_tt_waits_for_qc_no_show_w2` and `_test_receiver_due_first_giver_not_yet`: count the D-059 repeats.
* The profile test: RTG 30 s hold + 30 s positioning, NBIRTH `RtgPositioningS`.

**New**
* `_test_hold_derived_from_cycle`
* `_test_w4_starts_at_ready_to_pick`
* `_test_no_show_switch_parsing`
* `_test_no_show_switch_off_all_waits`: W1 to W4 at 10 × the time-out produce no report, and the stall backstop ends exactly one leg (D-060); switch on, no stall in waits
* `_test_no_show_clock_restarts_per_period`
* `_test_no_show_repeats_while_waiting` (D-059; replaces `_test_no_rearm_after_timeout`)
* `_test_rtg_positioning_before_w4_d058`
* `_test_margins_skip_no_show_when_off`
* `_test_import_spec_cycle_matches_constants`: a drift check. Every "`SimTiming.REALISTIC_{QC,RTG}_CYCLE_S` …, currently N s" figure in `docs/scenarios/import_v1.md` must equal the constants. It is skipped, with a note, when the docs folder is not next to the project.

`fast`-profile traces are byte-identical before and after this change (re-checked after D-058 to D-060). The seeds checked were 1, 7, 42 and 2024, across the single-leg, chain and profile traces.

## Outside the Godot client (BE asked for this; ownership is question A3)

* `apps/simulator-runner/runner.py`: `ALLOWED_ENVIRONMENT`.
* `apps/terminal-oes/terminal_oes/settings.py`: `SIMULATOR_SIM_SETTING_NAMES`.
* The cross-check tests `apps/simulator-runner/test_runner.py` and `apps/terminal-oes/execution/test_handoffs.py`.
* The docs `apps/simulator-runner/README.md` and `apps/terminal-oes/docs/UNS_CONTRACT.md`.
* Not done here, left with BE (A1): OES storing `NoShowTimeoutEnabled` and `RtgPositioningS` from NBIRTH, and `compose.yaml` / `.env.example` passing the key through.
