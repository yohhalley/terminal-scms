# Architecture

## Ownership

| Domain | Authoritative owner |
| --- | --- |
| Terminal layout, inventory, route plans, work queues | MiniTOS PostgreSQL |
| Released-order execution, assignments, commands | Terminal OES PostgreSQL |
| Live CHE telemetry and simulator state | Simulator, projected through Sparkplug B |
| Business event transport | HiveMQ MQTT |
| Time-series history | Timebase, after its separate deployment is complete |
| Process automation | FlowFuse-hosted Node-RED instances |

## Order flow

1. MiniTOS plans an order and route legs.
2. `POST .../orders/{id}/release/` changes the order to `released` and writes a `scms.order.released.v1` event into the same database transaction.
3. The MiniTOS outbox process publishes the event to HiveMQ.
4. Terminal OES consumes it idempotently and creates execution operations.
5. OES selects a fresh, powered-on, available CHE matching the required equipment type.
6. OES writes a command and MQTT outbox event in one transaction.
7. The simulator executes the command and publishes result milestones.
8. OES updates execution state and publishes `scms.execution.event.v1`.
9. MiniTOS consumes the event and atomically updates order, CHE, and container custody.

QoS 1 guarantees at-least-once delivery, not exactly once. UUID inbox records make duplicates harmless.

## Protocol split

- Sparkplug B and Eclipse Tahu semantics are used for node/device lifecycle and CHE telemetry.
- Versioned JSON events are used for business workflows such as order release, dispatch, pickup, drop, and completion.

Tahu is an SDK/reference implementation for Sparkplug. It is not an automatic database replication product.

