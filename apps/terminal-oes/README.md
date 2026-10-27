# Terminal OES

Terminal OES is the order execution and dispatch service for the SCMS sandbox. It consumes released MiniTOS orders and live CHE state from the MQTT unified namespace, assigns executable order legs, sends equipment commands, and publishes execution events back to MiniTOS.

## Responsibilities

- Mirror only released orders and their executable legs.
- Maintain an operational projection of CHE state, position, energy and freshness.
- Maintain verified Enterprise/Site configuration projections from MiniTOS.
- Own one on-demand simulation session per ready Site and provide its frozen bootstrap.
- Act as the Sparkplug Host Application, normalize CHE lifecycle/telemetry, and publish DCMD.
- Dispatch the next eligible leg transactionally.
- Keep an immutable MQTT inbox and transactional outbox.
- Publish command and execution events with idempotency identifiers.

It does not own terminal planning, inventory, route generation, or authoritative CHE master data. Those remain in MiniTOS.

The OES database is not a copy of MiniTOS. Released orders are immutable
execution snapshots; assignments, commands, reservations and dispatch decisions
belong to OES. Settled inventory and equipment master data remain authoritative
in MiniTOS and arrive through versioned contracts.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py test
.venv/bin/python manage.py runserver
```

Without `DATABASE_URL`, local commands use SQLite. Docker uses PostgreSQL.

## Processes

Run these as separate processes in addition to the web API:

```bash
.venv/bin/python manage.py run_mqtt_consumer
.venv/bin/python manage.py publish_outbox
```

Internal API requests use `X-OES-Token` when `OES_API_TOKEN` is configured.

Key endpoints:

- `GET /equipment/` authenticated operations console
- `GET /health/`
- `GET /api/orders/`
- `POST /api/orders/{id}/dispatch-next/`
- `GET /api/equipment/`
- `GET /api/enterprises/`
- `GET /api/sites/`
- `POST /api/sites/{id}/start-simulation/`
- `POST /api/simulation-sessions/claim/` restricted to the host runner
- `GET /api/simulation-sessions/{id}/bootstrap/` with a session token
- `POST /api/simulation-sessions/{id}/stop/`
- `POST /api/commands/{command_id}/event/`

### `POST /api/orders/{id}/dispatch-next/` (TCL-028)

Dispatches the order's next step as TCL-002 DCMDs (QC + TT paired on one
HandoffId, then TT→RTG once that hand-off has transferred). Optional body:
`{"equipment_id": <id>}` to name the CHE. Clients should branch on `code`,
not on `detail`, which is human text.

| Status | `code` | When |
|---|---|---|
| 201 | none | New DCMDs queued. |
| 200 | `nothing_to_dispatch`, `already_dispatched`, `waiting_for_handoff`, `earlier_operation_incomplete` | No-op re-call: a leg of the order is still dispatched/working **and** its simulation session is running and node-connected. Nothing new is sent. |
| 409 | `order_not_released` | The order is not released or in progress. |
| 409 | `nothing_to_dispatch`, `already_dispatched`, `waiting_for_handoff`, `earlier_operation_incomplete` | The same codes as the 200 row, but no leg of the order is in flight. |
| 409 | `simulation_not_running` | A leg is in flight, but its session is stopped or the Edge Node is offline (NDEATH), so the leg cannot progress. Restart or reset the simulation. |
| 409 | `operation_not_plannable` | The next operation (or the hand-off receiver leg) is not planned, e.g. blocked. |
| 409 | `no_simulation` | The Site has no running, connected simulation. |
| 409 | `no_equipment` | No fresh, available CHE fits the operation. |
| 409 | `dispatch_refused` | Any other service refusal without its own code. |
| 400 | `equipment_invalid` | The named `equipment_id` is not a fresh, available CHE of the right type in the active session. |
| 400 | serializer errors | Invalid body, e.g. `equipment_id: 0`. |
| 404 | none | Unknown order. |
| 401/403 | none | Missing or wrong `X-OES-Token`. |

Body shapes:

- 201: the order (as in `GET /api/orders/{id}/`) plus `"dispatched": true`.
- 200: the order plus `"dispatched": false`, `"code"` and `"detail"`. Here
  `detail` explains why nothing was sent; it is not an error. Check
  `dispatched`, not the presence of `detail`.
- 409 and 400 with a code: `{"code": "...", "detail": "..."}`.

See [docs/UNS_CONTRACT.md](docs/UNS_CONTRACT.md) for MQTT topics and payloads.

Manual console changes are recorded as debug overrides. A subsequent simulator
observation supersedes the override; issuing acknowledged simulator state
commands is intentionally separate future work.

Godot never reads this database or MiniTOS directly. The restricted host runner
claims an OES-created session, launches the allowlisted image, and passes only
the Site/session identity, configuration revision, MQTT identity, and a
short-lived bootstrap token. The OES container does not receive the Docker
socket.
