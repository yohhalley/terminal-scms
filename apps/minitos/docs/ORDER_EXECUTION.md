# Order execution contract

MiniTOS owns container inventory, the terminal graph, orders, route plans, and
planned order legs. An Order Execution System (OES) owns dispatch and runtime
execution. PostgreSQL is authoritative; MQTT is the event transport and the
REST callback is the authenticated control/fallback interface.

## Lifecycle

Creating a routed order leaves it in `planned`. A user releases it with:

```http
POST /locations/{location_id}/work_queues/{queue_id}/orders/{order_id}/release/
```

The OES can report execution events directly with the shared `MES_API_TOKEN`:

```http
POST /locations/{location_id}/work_queues/{queue_id}/orders/{order_id}/execution-callback/
X-MES-Token: <token>
Content-Type: application/json
```

```json
{
  "event": "leg_started",
  "leg_id": 42,
  "che_id": 17,
  "occurred_at": "2026-10-12T10:30:00Z"
}
```

Supported events are `order_started`, `leg_dispatched`, `leg_started`,
`container_picked_up`, `container_dropped`, `leg_completed`, `leg_failed`,
`leg_blocked`, `order_failed`, and `order_cancelled`. A drop also requires the
destination yard `position_id`.

Pickup and drop operations are transactional and idempotent. Pickup removes the
container from its static `Position`, sets the visit to `OnEquipment`, and records
the carrying CHE. Drop atomically rejects an occupied destination, assigns the
container to the destination, and returns the visit to `AtYard`.

## Configuration

Set the same secret in MiniTOS and terminal-oes:

```text
MES_API_TOKEN=<long-random-secret>
```

The callback deliberately does not accept normal user authentication in place
of the service token.

## MQTT unified namespace

The preferred asynchronous path uses a transactional inbox/outbox and QoS 1:

1. Releasing an order writes `scms.order.released.v1` to `IntegrationOutbox` in the release transaction.
2. `python manage.py publish_uns_outbox` publishes it to `scms/v1/terminal-{location_id}/tos/orders/{order_id}/released`.
3. Terminal OES consumes the order and publishes `scms.execution.event.v1` milestones.
4. `python manage.py run_uns_consumer` applies those milestones idempotently and records their UUIDs in `IntegrationInbox`.

MQTT is transport, not the database of record. MiniTOS PostgreSQL remains authoritative for orders and inventory.
