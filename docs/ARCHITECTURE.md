# Architecture

## Repository and deployment boundaries

Terminal SCMS is a monorepo, but it is not a monolithic process. MiniTOS and
Terminal OES have separate Docker images, databases, migrations, APIs, and
runtime responsibilities. The root Compose project assembles those services
into one installable product.

Optional resource-heavy capabilities should use deployment profiles:

- `core`: MiniTOS, Terminal OES, HiveMQ, and PostgreSQL.
- `automation`: FlowFuse and its hosted Node-RED applications.
- `historian`: Timebase.
- `ai`: a future local or external language-model gateway.

Only `core` is implemented today. Optional profiles will be added after their
host-specific prerequisites have been completed and measured.

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

## OES operational projections

OES does not copy the MiniTOS schema. Its execution database contains compact
read models and ledgers:

| OES model | Purpose | Authority |
| --- | --- | --- |
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
   terminal scope, aggregate identity/revision, source timestamp, correlation ID
   and causation ID. Schema-breaking changes receive a new version.
8. No bidirectional field synchronization is allowed. Each fact has one writer;
   other systems store clearly named projections.

## Order flow

1. MiniTOS plans an order and route legs.
2. `POST .../orders/{id}/release/` changes the order to `released` and writes a `scms.order.released.v1` event into the same database transaction.
3. The MiniTOS outbox process publishes the event to HiveMQ.
4. Terminal OES consumes it idempotently and creates execution operations.
5. OES filters by capability, power, availability, maintenance, freshness,
   operating scope and route feasibility before ranking candidates.
6. OES writes a command and MQTT outbox event in one transaction.
7. The headless simulator executes the command and publishes result milestones.
8. OES updates execution state and publishes `scms.execution.event.v1`.
9. MiniTOS consumes the event and atomically updates order, CHE, and container custody.

QoS 1 guarantees at-least-once delivery, not exactly once. UUID inbox records make duplicates harmless.

## Protocol split

- Sparkplug B and Eclipse Tahu semantics are used for node/device lifecycle and CHE telemetry.
- Versioned JSON events are used for business workflows such as order release, dispatch, pickup, drop, and completion.

Tahu is an SDK/reference implementation for Sparkplug. It is not an automatic database replication product.

## Headless simulator

The interactive `TerminalSandbox.tscn` remains available for local visual work.
The deployed service runs `source/headless/HeadlessSimulator.tscn` with Godot's
`--headless` option. It has no UI or renderer dependency and is configured by
environment variables. It reads static terminal/CHE configuration from MiniTOS,
subscribes to OES commands, simulates motion in geographic coordinates and
publishes Sparkplug lifecycle/telemetry plus versioned JSON command results.

Compose starts PostgreSQL and HiveMQ first, then the healthy Django services,
and finally Godot. Every process still implements retry behavior because startup
ordering does not protect against a service restart later.
