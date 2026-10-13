# Terminal SCMS

Terminal SCMS is a monorepo for the container-terminal management sandbox. One
checkout contains the planning system, order execution system, Godot simulator,
integration contracts, and Docker deployment configuration.

## Components

- **MiniTOS** owns planning, inventory, layout, routing, and operational master data.
- **Terminal OES** owns released-order execution, CHE assignments, commands, and execution history.
- **Godot simulator** runs headlessly, executes OES commands, and publishes live Sparkplug B telemetry and command results.
- **HiveMQ Community Edition** transports unified-namespace messages.
- **PostgreSQL** provides one authoritative database per Django application.
- **FlowFuse** is the planned process-automation layer.
- **Timebase** is the planned operational historian.

HiveMQ is an integration backbone, not a durable system of record. Each service
owns its database and uses idempotent inboxes plus transactional outboxes for
at-least-once messaging.

## Repository layout

```text
terminal-scms/
  apps/
    minitos/
    terminal-oes/
  clients/
    godot-open-terminal/
  docs/
  compose.yaml
  Makefile
```

The application histories were imported from their original repositories. The
services remain independently deployable even though they now release together.

## Start the core stack

Requirements are Docker Engine with Docker Compose and GNU Make. Make is a
convenience layer only; every operation remains available through ordinary
`docker compose` commands.

```bash
git clone https://github.com/yohhalley/terminal-scms.git
cd terminal-scms
make setup
```

Without Make:

```bash
cp .env.example .env
docker compose config --quiet
docker compose up --build --detach --wait
docker compose ps
```

Local endpoints:

- MiniTOS: http://localhost:8000/
- Terminal OES console: http://localhost:8010/equipment/
- Terminal OES health: http://localhost:8010/health/
- HiveMQ MQTT: `localhost:1883`

If port `1883` is already occupied, set `MQTT_PORT=1885` in `.env`. Containers
continue to use `hivemq:1883` on the internal Docker network.

Useful commands:

```bash
make help
make logs
make test
make migrations
make down
```

Create one MiniTOS account for the simulator credentials configured in `.env`,
and create an OES account for the protected operations console. The simulator
will keep retrying until its MiniTOS account exists.

`make down` retains named database, broker, and simulator session volumes. Do not add `--volumes`
unless the stored development data is intentionally disposable.

## Optional platform services

FlowFuse and Timebase remain staged integrations rather than pretending to be
portable single containers:

- FlowFuse self-hosting needs a reachable domain and wildcard DNS. Hosted
  Node-RED applications will connect to the SCMS HiveMQ endpoint.
- Timebase 2.0 is beta and its agent runbook requires interactive certificate,
  administrator-password, client-secret, and optional AI-provider steps.
- The interactive Godot scene remains available for local visual testing, while
  Compose runs a separate non-interactive Linux service scene.

See [Architecture](docs/ARCHITECTURE.md), [Linux deployment](docs/LINUX_DEPLOYMENT.md),
[FlowFuse](docs/FLOWFUSE.md), and [Timebase](docs/TIMEBASE.md).

Change every placeholder secret before exposing the stack outside a local
development machine.
