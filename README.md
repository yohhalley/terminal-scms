# Terminal OES

Terminal OES is the order execution and dispatch service for the SCMS sandbox. It consumes released MiniTOS orders and live CHE state from the MQTT unified namespace, assigns executable order legs, sends equipment commands, and publishes execution events back to MiniTOS.

## Responsibilities

- Mirror only released orders and their executable legs.
- Maintain a short-lived operational shadow of CHE state.
- Dispatch the next eligible leg transactionally.
- Keep an immutable MQTT inbox and transactional outbox.
- Publish command and execution events with idempotency identifiers.

It does not own terminal planning, inventory, route generation, or authoritative CHE master data. Those remain in MiniTOS.

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

- `GET /health/`
- `GET /api/orders/`
- `POST /api/orders/{id}/dispatch-next/`
- `GET /api/equipment/`
- `POST /api/commands/{command_id}/event/`

See [docs/UNS_CONTRACT.md](docs/UNS_CONTRACT.md) for MQTT topics and payloads.
