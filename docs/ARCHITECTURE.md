# Architecture

## Repository and deployment boundaries

Terminal SCMS is a monorepo, but it is not a monolithic process. MiniTOS and
Terminal OES have separate Docker images, databases, migrations, APIs, and
runtime responsibilities. The root Compose project assembles those services
into one installable product.

Optional resource-heavy capabilities should use deployment profiles:

- `core`: MiniTOS, Terminal OES, HiveMQ, and PostgreSQL.
- `simulation`: the allowlisted headless Godot image used by the host runner.
- `automation`: FlowFuse and its hosted Node-RED applications.
- `historian`: Timebase.
- `ai`: a future local or external language-model gateway.

The `core` stack and `simulation` image profile are implemented today. The
automation, historian, and AI profiles will be added after their host-specific
prerequisites have been completed and measured.

## Ownership

| Domain | Authoritative owner |
| --- | --- |
| Terminal layout, routing graph, equipment master, planned work and settled inventory | MiniTOS PostgreSQL |
| Released-order execution, detailed sequencing, reservations, assignments and commands | Terminal OES PostgreSQL |
| Live CHE observations and simulated physical motion | Simulator, projected into OES through MQTT |
| Business event transport | HiveMQ MQTT |
| Time-series history | Timebase, after its separate deployment is complete |
| Process automation | FlowFuse-hosted Node-RED instances |

The UNS is shared operational context, not a database and not an authority by
itself. Services never synchronize tables or write another service's database.
They exchange versioned facts and maintain projections designed for their own
decisions.

## Enterprise and Site identity

`Enterprise` is the stable company/tenant boundary. `Site` is the stable
execution boundary used by MiniTOS, OES, simulations, and MQTT. For the first
milestone, a Site has a one-to-one relationship with a terminal or inland-depot
`Location`; vessel, train, and truck Locations are not Sites. Existing data is
seeded under the editable `terminal-velocity` Enterprise.

Site UUIDs and codes are immutable integration identities. Location primary
keys remain in editor APIs as local implementation identifiers, but they are
never cross-system identity. All current business events use
`scms/v2/{enterprise_code}/{site_code}`. A disabled MiniTOS compatibility flag
can temporarily dual-publish released orders to `scms/v1`, but OES consumes
only the multi-Site namespace.

Publishing a valid layout increments `Site.configuration_revision`, freezes a
`SiteConfigurationSnapshot`, and writes
`scms.site.configuration.published.v1` to the MiniTOS outbox in the same
transaction. OES consumes that event, downloads the exact revision over its
authenticated HTTP endpoint, verifies the SHA-256 checksum and Site UUID, then
marks its `SiteProjection` ready. A stale or corrupt revision cannot replace a
newer ready projection.

## OES operational projections

OES does not copy the MiniTOS schema. Its execution database contains compact
read models and ledgers:

| OES model | Purpose | Authority |
| --- | --- | --- |
| `EnterpriseProjection` and `SiteProjection` | MiniTOS identity, configuration revision, checksum and readiness | MiniTOS owns identity/configuration; OES owns projection health |
| `ExecutionOrder` and `ExecutionOperation` | Immutable released-plan snapshot and live operation state | OES owns execution; MiniTOS owns the planning source |
| `EquipmentState` | Latest normalized CHE observation, freshness and debug override provenance | Simulator supplies observations; OES owns the dispatch projection |
| `PositionState` | Planned addition for occupancy, restriction and freshness | MiniTOS supplies settled inventory; OES owns transient availability |
| `ContainerExecutionState` | Planned addition for current observed custody | OES owns execution custody until MiniTOS accepts the result |
| `ResourceReservation` | Planned addition for CHE, container, slot and interface leases | OES only |
| `OperationalRestriction` | Planned addition for temporary blocks with source and expiry | OES for temporary restrictions; MiniTOS for durable planning restrictions |
| `DispatchDecision` | Planned candidate scores, rejection reasons and selected CHE | OES only |
| `ProjectionCheckpoint` | Planned source revision, replay position and projection health | OES only |

Completed execution records are retained for audit. High-rate telemetry belongs
in Timebase; OES retains the current dispatch state and meaningful transitions.

## Synchronization rules

1. MiniTOS publishes a frozen released-order snapshot. Changes after release
   require cancellation or a new version rather than mutating the accepted plan.
2. OES applies messages idempotently through its inbox and rejects older state
   observations when their source timestamp precedes its current projection.
3. OES persists command intent and assignment before publishing the command.
4. Godot publishes observations and command milestones; it never commits
   inventory or writes the MiniTOS database.
5. MiniTOS applies pickup, drop and completion evidence in transactional domain
   services. OES keeps an unreconciled state until that commitment is accepted.
6. MQTT retained messages may bootstrap current state, but recovery does not rely
   on broker retention alone. Snapshot/reconciliation endpoints and checkpoints
   rebuild projections after data loss or extended outage.
7. Every business event carries a globally unique event ID, schema version,
   Enterprise/Site scope, aggregate identity/revision, source timestamp, correlation ID
   and causation ID. Schema-breaking changes receive a new version.
8. No bidirectional field synchronization is allowed. Each fact has one writer;
   other systems store clearly named projections.
9. A Site is ready for simulation only after OES has verified the matching
   published snapshot revision. Imported CHE master records begin unavailable;
   live telemetry must establish execution availability.

## Order flow

1. MiniTOS plans an order and route legs.
2. `POST .../orders/{id}/release/` changes the order to `released` and writes a `scms.order.released.v1` event into the same database transaction.
3. The MiniTOS outbox process publishes the event to HiveMQ.
4. Terminal OES consumes it idempotently and creates execution operations.
5. OES filters by capability, power, availability, maintenance, freshness,
   operating scope and route feasibility before ranking candidates.
6. OES writes a Sparkplug DCMD and binary MQTT outbox event in one transaction.
7. The headless simulator validates and executes the DCMD, then publishes result
   milestones as DDATA metrics.
8. OES updates execution state and publishes `scms.execution.event.v1`.
9. MiniTOS consumes the event and atomically updates order, CHE, and container custody.

QoS 1 guarantees at-least-once delivery, not exactly once. UUID inbox records make duplicates harmless.

## Protocol split

- Sparkplug B and Eclipse Tahu semantics are used for node/device lifecycle,
  CHE telemetry, OES DCMD commands, and command-result metrics.
- Versioned JSON events under `scms/v2` are used for business workflows such as
  Site synchronization, order release, and settled execution evidence returned
  to MiniTOS.

Tahu is an SDK/reference implementation for Sparkplug. It is not an automatic database replication product.

Sparkplug device connectivity and CHE power are related but independent facts.
Per Sparkplug 3.0 section 6.4.26, DDEATH is valid when a device becomes
inaccessible, including after power-off. The SCMS simulator has direct knowledge
of simulated power state, so a participating CHE remains born and publishes
`State/PoweredOn=false` in DDATA. It publishes DDEATH only when the simulated
device becomes inaccessible. OES never infers the last physical power state
from DDEATH alone.

## Headless simulator

The interactive `TerminalSandbox.tscn` remains available for local visual work.
The deployed service runs `source/headless/HeadlessSimulator.tscn` with Godot's
`--headless` option. It has no UI or renderer dependency and is configured by
environment variables supplied by the restricted host runner. It retrieves a
frozen, checksummed bootstrap snapshot from OES using a short-lived,
session-scoped token. It never receives MiniTOS credentials. It subscribes to
Sparkplug NCMD/DCMD, simulates motion in geographic coordinates, and publishes
NBIRTH, DBIRTH, DDATA, DDEATH/NDEATH when appropriate, and command results.

Normal Compose startup excludes Godot. OES allows one active
`SimulationSession` per ready Site. A host-side runner claims that session and
launches the allowlisted image without exposing the Docker socket to OES. The
session-specific volume stores command idempotency records, in-flight commands,
and recent CHE state so a same-session restart resumes without executing a
duplicate DCMD.
