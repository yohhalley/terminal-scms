# SCMS UNS contract

The broker transports state and events. PostgreSQL remains the durable source of truth for MiniTOS and Terminal OES.

## Topic roots

All current business topics use the multi-Site namespace
`scms/v2/{enterprise_code}/{site_code}`. The version changes because the topic
scope and identity contract are different, not because the product is declaring
an MVP release. MiniTOS retains an opt-in dual-publish flag for short migrations,
but OES consumes only v2.

| Producer | Topic | Schema |
| --- | --- | --- |
| MiniTOS | `.../tos/orders/{order_id}/released` | `scms.order.released.v1` |
| OES | `.../oes/orders/{order_id}/execution` | `scms.execution.event.v1` |
| MiniTOS | `scms/v2/.../tos/site/created` | `scms.site.created.v1` |
| MiniTOS | `scms/v2/.../tos/configuration/published` | `scms.site.configuration.published.v1` |

Messages use QoS 1 and an `event_id` UUID. Consumers must be idempotent because
QoS 1 permits duplicate delivery. Business events are not retained.

The Site-created event creates a draft OES projection. A configuration-published
event carries the MiniTOS snapshot path, Site revision, schema, and SHA-256
checksum. OES retrieves that immutable document using `X-OES-Token`, verifies
its identity and checksum, and only then sets the projection to `ready`.

## Released order

```json
{
  "schema": "scms.order.released.v1",
  "event_id": "5cf69c7c-4ee0-4a03-a21e-803dfb2d693b",
  "occurred_at": "2026-10-12T10:00:00Z",
  "enterprise": {"id": "...", "code": "terminal-velocity"},
  "site": {"id": "...", "code": "test-terminal", "source_location_id": 1},
  "terminal": {"id": 1, "code": "TEST"},
  "data": {
    "order": {
      "id": 41,
      "status": "released",
      "priority": 20,
      "container_number": 101,
      "from_position": 11,
      "to_position": 12,
      "released_at": "2026-10-12T10:00:00Z"
    },
    "legs": [{
      "id": 4101,
      "sequence": 1,
      "leg_kind": "move",
      "status": "planned",
      "equipment_type": 4,
      "movement_mode": "road",
      "start_position": 11,
      "end_position": 12,
      "source_inventory_position_id": 111,
      "destination_inventory_position_id": 112,
      "source_latitude": 17.9401,
      "source_longitude": -102.1501,
      "destination_latitude": 17.9408,
      "destination_longitude": -102.1494
    }]
  }
}
```

## Sparkplug runtime boundary

OES is the Sparkplug Host Application. It publishes retained `ONLINE`/`OFFLINE`
state at `spBv1.0/STATE/{host_id}`, consumes the Godot Edge Node lifecycle, and
publishes device commands:

| Direction | Topic form | Purpose |
| --- | --- | --- |
| Godot to OES | `spBv1.0/{group}/NBIRTH/{node}` | Session, Site, revision, checksum, and `bdSeq` identity |
| Godot to OES | `spBv1.0/{group}/DBIRTH/{node}/{device}` | Register every participating CHE, including powered-off CHE |
| Godot to OES | `spBv1.0/{group}/DDATA/{node}/{device}` | Independent state facts, GPS, heading, energy, motion, and command results |
| Godot to OES | `spBv1.0/{group}/DDEATH/{node}/{device}` | Device is inaccessible to the Edge Node |
| Godot to OES | `spBv1.0/{group}/NDEATH/{node}` | Edge Node death certificate or graceful shutdown |
| OES to Godot | `spBv1.0/{group}/DCMD/{node}/{device}` | Idempotent, expiring equipment command |
| OES to Godot | `spBv1.0/{group}/NCMD/{node}` | Node controls such as rebirth |

NBIRTH must identify the active OES session, stable Site UUID, frozen
configuration revision/checksum, and matching `bdSeq`. OES rejects device data
before a valid NBIRTH. DBIRTH binds `CHE_{MiniTOS CHE ID}` only inside that
Site/session, so overlapping local IDs at another Site cannot collide.

Sparkplug 3.0 section 6.4.26 permits DDEATH after a device turns off and becomes
inaccessible. In this simulator, however, power is an observable equipment fact:
the CHE remains born and publishes `State/PoweredOn=false` in DDATA. DDEATH is
reserved for lost device accessibility. OES stores physical power separately
from `device_connected` and `data_valid` because DDEATH does not encode why the
device disappeared.

DCMD carries command, assignment, CHE, session, Site, configuration revision,
expiry, inventory-position, and geographic destination metrics. Godot records
the command ID before starting motion. Duplicate delivery replays the original
result ID and never creates a second physical action. A session-specific runtime
file preserves active commands and recent CHE state across process restart.

### Hand-offs, cancel and sim-time expiry (TCL-003a / TCL-028)

- **Expiry is sim time.** `Command/ExpiresAtMs` (Int64) is a `Sim/TimeMs`
  value: the anchor `Sim/TimeMs` + TTL. The anchor is the `Sim/TimeMs` of the
  result that caused the command (H1 transfer for H2, the time-out for cancels),
  else the highest `Sim/TimeMs` OES has seen on any NDATA/DDATA/DBIRTH of the
  session. The TTL is NBIRTH `Sim/Timing/CommandTtlS`, else
  `EQUIPMENT_COMMAND_TTL_SIM_SECONDS` (default 600). OES also sends it to the
  simulator as `SIMULATOR_COMMAND_TTL_S` in the claim environment.
- **Paired dispatch (T-006).** A hand-off pair shares `Command/HandoffId` and
  carries `Command/HandoffRole` (`giver`/`receiver`), `Command/CounterpartCheId`
  and `Command/TransferPositionId`. A QC giver gets `Command/Type=qc_discharge`.
  The TT->RTG hand-off (H2) is dispatched only after H1 has transferred (Q18):
  the TT gets `assign_handoff` (giver) and the RTG `execute_order_leg` (receiver).
- **Destination.** Every leg DCMD with a destination carries
  `Command/DestinationPositionId` (Int64, MiniTOS operational position),
  `Command/DestinationPositionCode` (String, its readable code, e.g. a block
  lane or slot) and, for a yard slot, `Command/DestinationInventoryPositionId`
  (Int64, the MiniTOS stack position chosen by MiniTOS), plus
  `Command/DestinationLatitude`/`Longitude`. These are the names Godot
  TCL-014b reads (contract rev 9.3); Godot prefers the inventory id, then the
  position id, and uses a seeded dummy slot only when none is sent (O6).
- **Cancel.** `Command/Type=cancel`, `Command/CancelCommandId` = the command to
  cancel, `Command/Reason`, plus the normal envelope (`Command/AssignmentId` of
  the target, `Command/ExpiresAtMs`). The cancel's own `Command/Id` is derived
  from the target, so a re-issued cancel is the same command.
- **Session scope (Q14).** Every NDATA, DDATA and DBIRTH must carry this
  session's `Session/Id`; a message without it or from another session is
  dropped and counted (`stale_message_count`) before sequence tracking or any
  CHE update. DDEATH carries only `seq` and is not checked. At NBIRTH every CHE
  of the site restarts idle at `Sim/StartEpochMs` (D-014/D-016).
- **Results.** Every result carries `Command/Result/Id` (applied once),
  `Session/Id`, `Sim/TimeMs`,
  `Event/TimeMs` and `Sim/EventSeq`. `arrived_at_source`,
  `arrived_at_destination` and `waiting_for_handoff` are informational. One
  `handoff_completed` per side; the first emits `container_transferred`.
  `handoff_timed_out` (QC giver) raises `handoff_exception` with
  `reason` `SIMULATOR_FAILURE` and `detail` `"no TT within <N> s"` (N =
  `SIMULATOR_TT_NO_SHOW_TIMEOUT_S`, default 300), and cancels the remaining
  legs (cancel `Command/Reason` = the same code); the order ends complete by
  exception (D-017). `reason` is an extensible enum (OES
  `HandoffExceptionReason`, MiniTOS `ExceptionReason`); MiniTOS rejects a
  missing reason (`reason_required`) or an unknown one (`reason_unknown`).
  `detail` is optional free text. A rejected result is stored with a reason and logged, never rolled
  back into the DDATA.
- **Aborted hand-offs.** A failed or cancelled leg cancels its pending
  hand-off and the counterpart leg (cancel DCMD). Stopping, a runner
  stop/failure, NDEATH after stop, or NBIRTH of a new session closes the
  session's pending hand-offs; a CHE is "busy" only for its own session.
- **Known gap: `Sim/EventSeq` ordering.** OES stores `Sim/EventSeq` on each
  result receipt but does not reorder or detect out-of-order results; it
  relies on QoS 1 per-topic ordering from one publisher. Follow-up story
  (TL review S-5).
- **One consumer.** Exactly one OES process consumes the Sparkplug topics for
  a site (fixed client id, persistent session). Two consumers would apply
  results concurrently; receipts are idempotent but ordering is not.
- **CHE name (D-015).** Execution events carry `che_name` (e.g. `QC01`) next
  to the numeric `che_id` that MiniTOS resolves; hand-off events add
  `from_che_name`/`to_che_name`.
- **MQTT.** The OES consumer uses MQTT 3.1.1 with clean session false and a
  fixed client id, so QoS 1 results queue while it is down (MQTT 5 is backlog).

Order release and execution lifecycle events are business messages and intentionally do not pretend to be Sparkplug device telemetry.

The OES equipment projection includes the independent power, work,
availability, dispatch and maintenance facts, true-north heading, geographic
position, speed, fuel/energy percentage, source timestamp and raw payload. OES
stores the latest observation and its freshness; Timebase will store the
high-frequency series.

## Synchronization invariants

- MiniTOS is the only writer of released planning snapshots and settled inventory.
- OES is the only writer of assignments, commands, reservations and execution state.
- Godot is the only simulator observation writer.
- A released snapshot is immutable. Replanning creates a replacement version.
- Physical completion and inventory commitment are distinct acknowledgements.
- Snapshot/reconciliation APIs repair projections; direct database access does not.
- Site configuration revisions only move forward; stale events are acknowledged
  idempotently and cannot replace a newer projection.
