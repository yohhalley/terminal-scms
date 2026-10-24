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

See [docs/UNS_CONTRACT.md](docs/UNS_CONTRACT.md) for MQTT topics and payloads.

Manual console changes are recorded as debug overrides. A subsequent simulator
observation supersedes the override; issuing acknowledged simulator state
commands is intentionally separate future work.

Godot never reads this database or MiniTOS directly. The restricted host runner
claims an OES-created session, launches the allowlisted image, and passes only
the Site/session identity, configuration revision, MQTT identity, and a
short-lived bootstrap token. The OES container does not receive the Docker
socket.
