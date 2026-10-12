# SCMS UNS contract

The broker transports state and events. PostgreSQL remains the durable source of truth for MiniTOS and Terminal OES.

## Topic root

All business topics use `scms/v1/{terminal_code}`. Topic names identify ownership:

| Producer | Topic | Schema |
| --- | --- | --- |
| MiniTOS | `.../tos/orders/{order_id}/released` | `scms.order.released.v1` |
| Simulator | `.../simulator/che/{che_id}/state` | `scms.equipment.state.v1` |
| OES | `.../oes/che/{che_id}/command` | `scms.equipment.command.v1` |
| Simulator | `.../simulator/commands/{command_id}/result` | `scms.command.result.v1` |
| OES | `.../oes/orders/{order_id}/execution` | `scms.execution.event.v1` |

Messages use QoS 1 and an `event_id` or `command_id` UUID. Consumers must be idempotent because QoS 1 permits duplicate delivery. Events are not retained. Current equipment state may be retained after the publisher and expiry policy are agreed.

## Released order

```json
{
  "schema": "scms.order.released.v1",
  "event_id": "5cf69c7c-4ee0-4a03-a21e-803dfb2d693b",
  "occurred_at": "2026-10-12T10:00:00Z",
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
      "end_position": 12
    }]
  }
}
```

## Telemetry boundary

Sparkplug B remains the preferred CHE telemetry contract (`NBIRTH`, `DBIRTH`, `DDATA`, `DDEATH`, `NDEATH`). The JSON equipment-state topic is the normalized operational projection consumed by the OES. A bridge may produce it from Sparkplug metrics until the OES has a production-grade Tahu decoder.

Order release and execution lifecycle events are business messages and intentionally do not pretend to be Sparkplug device telemetry.

