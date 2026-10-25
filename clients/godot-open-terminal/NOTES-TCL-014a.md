# TCL-014a – Godot QC→TT discharge, seed and sim clock: notes and open points

Branch `TCL-014a-qc-tt-discharge` (local only). Product decisions are not made
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
  NBIRTH publishes `Sim/Seed`, `Sim/RunId`, `Sim/TimeScale`, `Sim/FixedStepMs`,
  `Sim/StartEpochMs`. Every result carries `Session/Id`, `Sim/TimeMs`,
  `Event/TimeMs = Sim/StartEpochMs + Sim/TimeMs`, and `Sim/EventSeq`.
- **QC discharge (D-012)**: `idle -> commanded -> holding -> [waiting_for_tt] -> done`.
  The container appears on the QC when the command arrives (`acknowledged`,
  `started`, `container_picked_up` at the same Sim/TimeMs). Then comes a seeded
  whole-second hold wait (default 10..30 s). The deposit happens at
  max(hold end, TT ready): `container_dropped`, `handoff_completed`
  (HandoffRole=giver) and `completed`. The TT then emits `container_picked_up`
  and `handoff_completed` (HandoffRole=receiver) and drives to its destination.
  There is no pick phase and no timed lift, and no `SIM_QC_TIMED_PICK` flag
  (one never existed in the code).
- **Hold wait config**: `SIMULATOR_QC_WAIT_MIN_S` / `SIMULATOR_QC_WAIT_MAX_S`
  (aliases `SIM_QC_WAIT_*`). The step-specific
  `SIMULATOR_QC_HOLD_WAIT_MIN_S` / `_MAX_S` win over those. An invalid range
  logs a warning and keeps the previous range.
- **Pairing**: by `Command/HandoffId` only, never by container. At most one
  active command per side per HandoffId; a second one fails with
  "HandoffId is already held by another active command". The TT waits at the
  source only when the command has a HandoffId. Legacy legs behave as before.
- **Types**: missing `Command/Type` means `execute_order_leg`. Unknown types
  fail with "Unsupported command type: X".
- **Results**: QoS 1, persisted ordered outbox, replayed in order after
  reconnect or rebirth. Result ids are derived from run id, session, command,
  event and event seq. The outbox and journal are pruned only on session
  end or reset; there is no ack protocol.
- **Device id**: legacy `CHE_{id}` by default (current OES addresses that).
  `SIMULATOR_DEVICE_ID_MODE=name` uses MiniTOS `Che.name` (e.g. `QC01`). The
  mapping lives only in `source/headless/SimIds.gd`.
- **mqtt.gd**: the packet id wraps 65535 -> 1 and is never 0 (approved by the Tech Lead).

## Deviations from the contract proposal

- The QC pick wait was removed per D-012. This supersedes the earlier
  "receive, wait, take, wait, deposit" flow; the hold wait now uses wait index 0.
- `Command/ExpiresAtMs` is still checked against the wall clock, only on
  receipt. Sim-time waits after acceptance are not subject to it.
- The TT readiness gate checks HandoffId, the TT phase (`at_source_waiting`)
  and the counterpart ids. It has no geographic check.

## Open points (need a decision or an OES or backend change)

1. **D-013 no-show (5 min sim time)**: Godot does not time out on its own.
   The QC stays in `waiting_for_tt` with the container until OES acts. Still open:
   - the exact message OES sends to make Godot clear the container from the QC
     (a cancel DCMD? a new command type?)
   - the result event Godot emits when it clears the container
   - whether the 300 000 ms is measured by OES on Sim/TimeMs (needs Sim/TimeMs
     on periodic DDATA, see 6)
2. OES `apply_command_event` rejects `arrived_at_source`,
   `waiting_for_handoff` and `handoff_completed` ("Unsupported command event").
   It does not know `qc_discharge` and has no CHE→CHE transfer. These are needed
   before end-to-end use.
3. `apps/simulator-runner/runner.py` `ALLOWED_ENVIRONMENT` does not pass the new
   variables through (SIMULATOR_SEED, RUN_ID, FIXED_STEP_MS, TIME_SCALE if
   absent, START_EPOCH_MS, DEVICE_ID_MODE, QC_*WAIT*). Not changed here.
4. OES `EQUIPMENT_COMMAND_TTL_SECONDS` (30 s, wall clock) vs sim-time waits at
   high or low time scale.
5. At high time scale, the 2 s sim-time cadence for a working CHE can flood the broker.
6. Periodic equipment DDATA has no `Sim/TimeMs`; only results do.
7. PUBACK is not tracked: an outbox item counts as delivered once handed to
   the MQTT client.
8. Assumed names: the `Command/Result/MoveKind` metric (echo-only) and the
   bootstrap fields `session.seed` and `session.start_epoch_ms`.
   `Sim/ScenarioId` is not published.
9. There is no check that the target CHE of `qc_discharge` is a QC.
10. A bad container-number format (`^[A-Z]{3}[UJZR][0-9]{7}$`) is a hard
    reject. The check digit is left to MiniTOS.
11. The default seed `1` is arbitrary.
12. Out of scope: `qc_load` (S1 is discharge only) and the existing cancel bug.
13. Known on main (not caused here): TT.tscn `craft_cargoA.glb` parse error and
    "Parameter m is null" in headless test output. gdlint/gdformat are not
    clean on main; the new files (SimIds, SimClock, SimRandom, QcDischarge)
    pass gdlint, but HeadlessSimulator keeps the existing line-length style.
