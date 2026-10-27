# TCL-014a – Godot QC→TT discharge, seed and sim clock: notes and open points

Branch `TCL-014a-qc-tt-discharge` (PR #15). Contract: TCL-002 rev 9.3. Product decisions are not made
here; anything not covered by an explicit decision is listed as open.

## What the simulator does now

- **Seed**: `SIMULATOR_SEED` (alias `SIM_SEED`, default `1`; bootstrap
  `session.seed` overrides). Each random draw reseeds one
  `RandomNumberGenerator` from FNV-1a(seed | CHE device id | command key | wait
  index), so draws do not depend on order or frame timing. No `randomize()` /
  `randi()` in the headless path.
- **Sim clock**: fixed step `SIMULATOR_FIXED_STEP_MS` (default 100 ms) and
  `SIMULATOR_TIME_SCALE` / bootstrap `session.time_scale`. Frame delta only sets
  how many fixed steps run (max 1000 per frame; any backlog beyond that is dropped).
  NBIRTH publishes `Contract/Version` (`tcl-002.1`), `Sim/Seed`, `Sim/RunId`,
  `Sim/ScenarioId`, `Sim/TimeScale`, `Sim/FixedStepMs` and `Sim/StartEpochMs`
  (Int64 epoch ms). `Sim/ScenarioId` comes from bootstrap `session.scenario_id`,
  else `SIMULATOR_SCENARIO_ID` (allowlisted by the runner), else empty. Every
  result carries `Session/Id`, `Sim/TimeMs`, `Event/TimeMs = Sim/StartEpochMs +
  Sim/TimeMs` (Int64) and `Sim/EventSeq`.
- **QC discharge (D-012)**: `idle -> commanded -> holding -> [waiting_for_tt] -> done`.
  The container appears on the QC when the command arrives (`acknowledged`,
  `started`, `container_picked_up` at the same Sim/TimeMs). The
  `container_picked_up` result carries `Command/Result/Source = VESSEL` (T-007 Q24). Then comes a seeded
  whole-second hold wait (default 10..30 s). The deposit happens at
  max(hold end, TT ready). A hand-off is not a drop or a pick: the QC emits
  `handoff_completed` (HandoffRole=giver) and `completed`, and the TT emits
  `handoff_completed` (HandoffRole=receiver) and drives to its destination.
  Neither side emits `container_dropped` / `container_picked_up` for the
  transfer (Q8-R5: OES rejects a giver drop; BE review item 2).
  There is no pick phase and no timed lift, and no `SIM_QC_TIMED_PICK` flag
  (one never existed in the code).
- **Hold wait config**: `SIMULATOR_QC_WAIT_MIN_S` / `SIMULATOR_QC_WAIT_MAX_S`
  (aliases `SIM_QC_WAIT_*`). The step-specific
  `SIMULATOR_QC_HOLD_WAIT_MIN_S` / `_MAX_S` win over those. An invalid range
  logs a warning and keeps the previous range.
- **Cancel (T-007, fixes the cancel bug)**: a DCMD with `Command/Type = cancel`
  goes through the same envelope checks (session, site, revision, ExpiresAtMs,
  AssignmentId). It cancels the command named in `Command/CancelCommandId`,
  which is **required**: OES always sets it (BE interface review, TCL-003a), so
  the old "cancel the active command" fallback was removed. A cancel without it
  fails with `cancel_target_missing`. The active command stops
  and is cleared, and the CHE goes idle with no container. `cancelled` is
  published for the **cancelled** command id, with `Work/State=idle` and
  `Carrying/ContainerVisitId=0`. The CHE then accepts the next command.
  - A redelivered cancel replays the same `cancelled` result and never cancels
    a newer command.
  - A cancel for another command fails with `cancel_target_mismatch`.
  - A cancel on an idle CHE fails with `no_active_command`.
- **TT no-show (D-013 / T-007)**: after `Sim/Timing/TtNoShowTimeoutS` (default
  300 s sim time, env `SIMULATOR_TT_NO_SHOW_TIMEOUT_S` / `SIM_TT_NO_SHOW_TIMEOUT_S`,
  whole seconds > 0) in `waiting_for_tt`, the QC publishes `handoff_timed_out`
  once, with the HandoffId. It keeps the container and stays `waiting_for_tt`
  until OES cancels. A TT that becomes ready after the time-out gets no
  transfer. OES cancels both legs and Godot clears the QC through the cancel handler.
  NBIRTH also publishes `Sim/Timing/QcHoldMinS`, `Sim/Timing/QcHoldMaxS` and
  `Sim/Timing/TtNoShowTimeoutS`.
- **Command expiry in sim time (Tech Lead)**: `Command/ExpiresAtMs` is a
  **Sim/TimeMs** value, not wall-clock epoch ms. A DCMD is rejected with
  `command_expired` when `ExpiresAtMs <= Sim/TimeMs` on receipt. A DCMD with no
  (or a non-positive) `ExpiresAtMs` gets the deadline receipt + `Sim/Timing/CommandTtlS`.
  The TTL defaults to 600 s and is set via env `SIMULATOR_COMMAND_TTL_S` /
  `SIM_COMMAND_TTL_S`, whole seconds > 0. NBIRTH publishes it. Expiry is only
  checked on receipt; an accepted command does not expire mid-run.
- **Pairing**: by `Command/HandoffId` only, never by container. At most one
  active command per side per HandoffId; a second one fails with
  `handoff_already_active`, as does a hand-off command for a CHE that already
  holds one (contract §3.2). The TT waits at the
  source only when the command has a HandoffId. Legacy legs behave as before.
- **Types**: missing `Command/Type` means `execute_order_leg`. Unknown types
  fail with `unsupported_command_type`.
- **Failure reasons (S15-6)**: `Command/Result/Reason` on `failed` is a machine
  code, the readable text goes in `Command/Result/Detail`. Codes:
  `unknown_command_target`, `session_mismatch`, `site_mismatch`,
  `configuration_revision_mismatch`, `command_expired`, `assignment_id_missing`,
  `unsupported_command_type`, `che_busy`, `container_number_invalid`,
  `destination_missing`, `handoff_id_missing`, `container_visit_id_missing`,
  `handoff_already_active`, `che_not_executable`, `cancel_target_missing`,
  `no_active_command`, `cancel_target_mismatch`. Only `unsupported_command_type`
  and `handoff_already_active` are named in the contract; the rest are new
  (open point 14).
- **Hand-off results (S15-3/S15-4)**: both `handoff_completed` results carry
  `Command/Result/FromCheId` (giver), `ToCheId` (receiver) and
  `TransferPositionId`, plus `PositionId` = the same lane. The lane is
  `Command/TransferPositionId` as sent by OES; only when OES sends none does the
  QC fall back to its destination position and the TT to its source position.
  Results that carry `Work/State` also carry `Carrying/ContainerVisitId`.
- **Results (contract §5.2, B15-2)**: every result goes into a persisted
  journal (`_result_outbox`) and is published with QoS 1. An entry stays
  pending until the broker's PUBACK for its packet id arrives. On a disconnect
  (or a restart) every pending entry is resent after the next connect, in
  `Sim/EventSeq` order, with the same `Result/Id`. Acked entries stay in the
  journal until session end/reset (Q20). A redelivered DCMD replays its
  recorded result (same `Result/Id` and `Sim/EventSeq`). A rebirth only sends
  what is still pending: replaying the whole session journal after an OES
  rebirth request (§5.2 item 4) is S2 (Tech Lead review B15-2).
- **Result ids (S15-1)**: `Result/Id = uuid5(NS_TCL002, "{Session/Id}:{Sim/EventSeq}")`
  with `NS_TCL002 = 5db85d9e-b37c-5c1a-9501-e4cbb091c5b9`
  (= uuid5(NAMESPACE_URL, `urn:scms:contract:tcl-002:result-id`); the contract
  names the namespace but gives no value, open point 15).
- **Device id**: MiniTOS `Che.name` (e.g. `QC01`) by default
  (`SIMULATOR_DEVICE_ID_MODE=name`); OES binds whatever id DBIRTH registers.
  `SIMULATOR_DEVICE_ID_MODE=legacy` keeps the old `CHE_{id}`.
  `SIMULATOR_DEVICE_ID_MODE=name` uses MiniTOS `Che.name` (e.g. `QC01`). The
  mapping lives only in `source/headless/SimIds.gd`. A name that is not a valid
  Sparkplug id (`/`, `+`, `#`, outer spaces) is still made valid (`TT/1` ->
  `TT_1`) so the topic works, but a warning naming both forms is logged once
  per name (Q11: no silent normalisation, S15-8).
- **mqtt.gd**: the packet id wraps 65535 -> 1 and is never 0 (approved by the Tech Lead).

## Deviations from the contract proposal

- The QC pick wait was removed per D-012. This supersedes the earlier
  "receive, wait, take, wait, deposit" flow; the hold wait now uses wait index 0.
- `Command/ExpiresAtMs` moved from the wall clock to sim time (Tech Lead).
- The TT readiness gate checks HandoffId, the TT phase (`at_source_waiting`)
  and the counterpart ids. It has no geographic check.

## Open points (need a decision or an OES or backend change)

1. **Cancel DCMD shape**: confirmed against the BE branch TCL-003a
   (`queue_cancel_dcmd`): `Command/Type = cancel`, required
   `Command/CancelCommandId`, `Command/Reason`, the target's AssignmentId and a
   sim-time ExpiresAtMs. Still to land in the contract text. The cancel DCMD's own id gets no
   result of its own: only the cancelled command gets `cancelled`. A failed
   cancel is reported on the cancel's id.
   `Command/Result/Source` (value `VESSEL`) is an assumed metric name.
   `handoff_timed_out` must also be accepted by OES `apply_command_event`.
2. OES `apply_command_event` rejects `arrived_at_source`,
   `waiting_for_handoff`, `handoff_completed` and `handoff_timed_out` ("Unsupported command event").
   It does not know `qc_discharge` and has no CHE→CHE transfer. These are needed
   before end-to-end use.
3. `apps/simulator-runner/runner.py` now allowlists the new `SIMULATOR_*`
   settings explicitly (SEED, RUN_ID, FIXED_STEP_MS, START_EPOCH_MS,
   DEVICE_ID_MODE, QC_WAIT_MIN_S/MAX_S, QC_HOLD_WAIT_MIN_S/MAX_S,
   TT_NO_SHOW_TIMEOUT_S, COMMAND_TTL_S; TIME_SCALE was already there). It is a
   closed list rather than a prefix, so unknown keys still fail the launch.
   `SIM_*` aliases are local-only and are rejected by the runner.
   - Open: the runner only forwards what OES puts in the claim `environment`;
     OES does not set these yet.
   - `SIMULATOR_DEFAULT_SPEED_MPS` (set in compose.yaml) is now allowlisted too.
   - The timing values are env-only; the proposal wants them from
     scenario/bootstrap (§5.1). Not changed here.
   - `SIMULATOR_SCENARIO_ID` was added for NBIRTH `Sim/ScenarioId`.
4. Resolved: OES now sends `Command/ExpiresAtMs` as a sim-time deadline
   (`Sim/TimeMs` at dispatch + TTL), fixed on the BE branch TCL-003a (PR #13).
   - Open: whether a DCMD without ExpiresAtMs should rather be rejected. Godot
     applies the default TTL.
   - Open: whether a running command should also expire.
5. At high time scale, the 2 s sim-time cadence for a working CHE can flood the broker.
6. Resolved: periodic equipment DDATA (and DBIRTH) now carries `Sim/TimeMs` (sim clock at
   publish) next to `Session/Id`, so OES can use it as its sim-time source. Idle CHE
   publish every 20 s sim time, working CHE every 2 s. `Event/TimeMs` is still only
   on results.
7. Resolved (B15-2): PUBACK is tracked per journal entry (see Results). Still
   S2: whole-journal replay after an OES-requested rebirth, and OES-side
   EventSeq gap detection.
8. Assumed names: the `Command/Result/MoveKind` metric (echo-only) and the
   bootstrap fields `session.seed`, `session.start_epoch_ms` and
   `session.scenario_id`.
9. There is no check that the target CHE of `qc_discharge` is a QC.
10. A bad container-number format (`^[A-Z]{3}[UJZ][0-9]{7}$`, contract/OES/
    MiniTOS categories; `R` is no longer accepted) is a hard
    reject. The check digit is left to MiniTOS.
11. The default seed `1` is arbitrary.
12. Out of scope: `qc_load` (S1 is discharge only).
13. Known on main (not caused here): TT.tscn `craft_cargoA.glb` parse error and
    "Parameter m is null" in headless test output. gdlint/gdformat are not
    clean on main; the new files (SimIds, SimClock, SimRandom, QcDischarge)
    pass gdlint, but HeadlessSimulator keeps the existing line-length style.
14. New failure codes (see Failure reasons): only `unsupported_command_type`
    and `handoff_already_active` come from the contract. The other codes need
    adding to the contract and a check with the Backend Engineer. OES stores
    `Reason` as received and does not branch on it today.
15. `NS_TCL002` has no value in the contract; Godot fixes it as above. OES only
    compares ids for equality, so it needs no change, but the value should be
    written into contract §5.2.
16. Known, not in this PR: `Identity/Id` is still Int32 on DBIRTH (contract
    says Int64, N15-4), and the S15-7 pairing checks (CounterpartCheId required
    on `qc_discharge`, receiver role, TT leg with HandoffId but no source) are
    not done.
