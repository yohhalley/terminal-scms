# TCL-014b: TT to the block, RTG takes the container to a dummy slot

Branch `TCL-014b-tt-rtg-block` (PR #17), based on TCL-014a (PR #15) and merged
with its review fixes (no rebase). Pulled forward from
S2. RTG yard-to-truck is out of scope. This file records what was built, the
assumptions made, and the open points. No product decisions were taken here;
items marked **PO** need a decision through the Tech Lead.

## What was built

- `source/headless/RtgStack.gd`: a pure, serialisable RTG state machine that
  mirrors `QcDischarge.gd`:
  contract rev 9.3 §3.4 canonical states
  `idle -> gantry_to_bay -> [waiting_for_tt] -> picking_from_tt -> holding -> depositing_to_stack -> idle`.
  - `gantry_to_bay` (ack + started) takes zero sim time; no gantry travel.
  - `waiting_for_tt` is canonical (Tech Lead decision (a), rev 9.3, mirrors
    the QC), kept as its own state in the `RtgStack.WAITING_FOR_TT` constant.
  - The pick is `handoff_completed` (receiver), published with `Work/State` set
    to `picking_from_tt`.
  - Then comes the seeded hold (whole seconds, default 10..30).
  - Then `container_dropped` (Work/State `depositing_to_stack`) with the slot,
    followed by `completed` (Work/State `idle`); the RTG is idle again.
- The RTG leg follows contract rev 8 (§3.2, §4 step 10): an `execute_order_leg`
  sent to an RTG CHE (equipment type `RTG`/`ARTG`) with a `Command/HandoffId`.
  - It needs `Command/ContainerVisitId`. `Command/HandoffRole` must be
    `receiver` when it is sent.
  - No `rtg_stack` Command/Type was added, because the contract keeps
    `execute_order_leg` for the RTG.
  - An `execute_order_leg` to an RTG without a HandoffId keeps the old
    drive-to-destination behaviour.
- The TT after H1:
  - It drives to its leg destination (the block lane, BLOCK01-TL), publishes
    `arrived_at_destination`, and waits there in `at_destination_waiting`.
  - It publishes `waiting_for_handoff` once it holds H2.
  - It no longer drops the container itself; this changes TCL-014a, see A3.
- A new DCMD `Command/Type = assign_handoff` gives the active TT leg its H2.
  It follows the BE TCL-003a interface (`queue_assign_handoff_dcmd`) exactly:
  base envelope with `Command/AssignmentId` = the TT leg's own assignment,
  plus `Command/LegId` (the giver leg), `Command/ContainerVisitId`,
  `Command/ContainerNumber`, `Command/HandoffId`, `Command/HandoffRole = giver`
  (required), `Command/CounterpartCheId` (the RTG pk) and
  `Command/TransferPositionId`. There is no `AssignCommandId` and no
  `TransferPositionCode`.
  - The target is the TT's active leg with that AssignmentId; a different
    AssignmentId, or a LegId / ContainerVisitId that differs from the leg's
    (when both are set), is rejected on the assign id.
  - It is rejected with `handoff_already_active` while H1 is still running
    (Q18: TT→RTG is dispatched only after QC→TT completes), and also when the
    TT already holds an H2. A TT holds at most one active hand-off.
  - H1 cannot be reused as H2.
  - On success nothing is published on the assign id (the BE test sequence has
    no ack there, and OES would route an `acknowledged` into
    `apply_command_event`). A silent outcome is recorded so a redelivered
    assign is deduplicated without a reply.
  - At H2 the TT's `handoff_completed` (giver, PositionId =
    `TransferPositionId`) is published on the **assign_handoff command id**, and
    `completed` on the TT leg id, as in the BE test.
- The gate matches the RTG to a TT that holds the same HandoffId as giver, is
  in `at_destination_waiting`, and whose counterparts match where named. It
  pairs on HandoffId only, never by container (T-006).
- The hand-off happens in one fixed step, giver first (the same order as QC→TT):
  - TT: `handoff_completed` (giver, on the assign id) and `completed` (on the
    leg id), then `Work/State` idle with
    `Load` 0. The TT is empty and idle.
  - RTG: `handoff_completed` (receiver), with `Load` = the visit.
  - Both carry H2, counterpart ids and the same `Event/TimeMs`.
- Slot (O6, D-009):
  - OES always sends the RTG leg's destination (MiniTOS picks the slot).
    `Command/DestinationInventoryPositionId` / `Command/DestinationPositionId`
    and `Command/DestinationPositionCode` are echoed in `container_dropped`
    and `completed` as `Command/Result/PositionId` / `PositionCode`.
  - Only when none is sent, the slot is a dummy, `Command/Result/PositionCode =
    BLOCKxx-DUMMY-nn`, with nn in 1..20 drawn from the seed (per RTG and leg).
    The block comes from the lane code (`BLOCK01-TL` → `BLOCK01`).
- D-013 analogue: an RTG in `waiting_for_tt` publishes `handoff_timed_out` once,
  after `Sim/Timing/TtNoShowTimeoutS` (the same setting, default 300 s). After
  that, a late TT gets no transfer and OES must cancel.
- Cancel works as for the QC: `cancelled` with `Work/State` idle, and any
  container is cleared. A TT waiting at the lane keeps its container and H2, and
  a new RTG command with the same H2 is accepted.
- D-011: an RTG command is rejected with "RTG does not serve block X" when the
  CHE names its block (`block_code` or `current_asset_name`) and the lane's block
  differs. The OES bootstrap does not send either field today (see O4).
- Config:
  - `SIMULATOR_RTG_WAIT_MIN_S` / `_MAX_S` and `SIMULATOR_RTG_HOLD_WAIT_MIN_S` /
    `_MAX_S`, with `SIM_*` aliases.
  - `configure_rtg_wait()`.
  - NBIRTH `Sim/Timing/RtgHoldMinS` and `RtgHoldMaxS`.
  - The four env keys are on the simulator-runner allowlist.
- New result metrics: `Command/Result/PositionCode` (String, for the dummy slot)
  and `Work/TransferPositionCode` (only when a command names it; BE sends none).

## Assumptions

- A1: The RTG does not travel; contract `gantry_to_bay` is not modelled. The RTG
  is assumed to be at the lane. One hold is used after the pick, symmetric with
  the QC's single hold.
- A2: The RTG `Work/State` names follow contract rev 9.3 §3.4 (O2 resolved).
  The command field name `Command/DestinationPositionCode` is assumed (the
  contract §4 step 10 says "PositionId/PositionCode" without the metric name).
- A3: A TT that received a container through a hand-off now always waits at its
  destination for the RTG, as in contract §4 steps 11-12.
  - In TCL-014a it dropped the container at the destination and completed. The
    TCL-014a end-to-end tests were updated to match.
  - Plain legs without a HandoffId are unchanged.
  - The TT has no time-out while it waits for an RTG; only an OES cancel ends it.
- A4: Following the contract (Q8-R5), a hand-off emits `handoff_completed`
  only on both sides: no `container_dropped` from a giver (QC at H1, TT at H2)
  and no `container_picked_up` from a receiver (TT at H1, RTG at H2). TCL-014a
  was changed to match (BE review item 2). Removing the TT receiver's
  `container_picked_up` at H1 is my reading of "handoff_completed only"; OES
  treats it as informational, so either way is safe.
- A5: The `assign_handoff` shape now comes from the BE branch TCL-003a (read
  only). It passes the same session, site, revision, expiry and AssignmentId
  checks as any DCMD. `ExpiresAtMs` on it is checked on receipt only.
- A6: When switched on (O7), the RTG no-show clock starts when the RTG enters `waiting_for_tt`, that
  is, on the first step after its command when no TT is waiting. If OES commands
  the RTG long before the TT can arrive, 300 s may fire on a TT that is still
  driving.

## Open points

- O1 (**PO**, via the Tech Lead): RTG timing. The contract gives none, so the
  default is 10..30 s like the QC hold, and it is configurable. Should there be
  a separate gantry/travel wait before the pick?
- O2: resolved (rev 9 / 9.3): names aligned to §3.4, `waiting_for_tt`
  canonical.
- O3: resolved. TCL-014a no longer emits `container_dropped` (QC giver) or
  `container_picked_up` (TT receiver) for the hand-off (A4).
- O4 (backend): D-011 cannot be enforced, because the OES bootstrap has no RTG
  block. MiniTOS has `Che.current_asset` (RTG01 → the first block). Adding
  `current_asset_name` (or `block_code`) to the bootstrap CHE switches the check
  on with no Godot change. The BE RTG leg also carries no
  `TransferPositionCode`, so the lane-block side of the check (and the dummy
  slot's block) falls back to the RTG's block, else `BLOCK01`. The field is
  still read when present.
- O5: resolved by the BE interface (A5). Still to land in the contract text:
  `assign_handoff` fields, results on the assign id (`handoff_completed`
  only, or `failed`), and no ack.
- O6: resolved (rev 9): OES always sends the RTG destination; Godot echoes it,
  dummy slot only when absent. Confirm the `Command/DestinationPositionCode`
  metric name (A2).
- O7 (**PO**): should the D-013 time-out apply on the RTG side at all, and
  with which start point (A6)? Until decided it sits behind its own switch,
  `SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED` (alias `SIM_RTG_NO_SHOW_TIMEOUT_ENABLED`,
  runner allowlisted), default **off** (D17-1): the RTG waits in `waiting_for_tt`
  until the TT arrives or OES cancels. On, it uses `Sim/Timing/TtNoShowTimeoutS`.
  NBIRTH reports it as `Sim/Timing/RtgNoShowTimeoutEnabled` (Boolean; name assumed).
- O8: should a TT waiting at the block for an RTG time out (A3)?
- O10: RTG-leg and `assign_handoff` failures now use the 014a code/detail split
  (S15-6). New codes, not yet in the contract list: `handoff_role_invalid`,
  `rtg_block_mismatch`, `handoff_target_mismatch` (assign_handoff for another
  leg / assignment / visit, or for a crane), `counterpart_che_missing`,
  `handoff_not_ready` (TT has no completed receiver hand-off), `handoff_id_reused`.
  Reused 014a codes: `container_visit_id_missing`, `no_active_command`,
  `handoff_id_missing`, `handoff_already_active`.
- O9: `HeadlessSimulator.gd` is now over gdlint's 1000-line limit. Its existing
  style already fails gdlint on main. A later split (hand-off plumbing into its
  own script) is suggested. `RtgStack.gd` is lint-clean.
