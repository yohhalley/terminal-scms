# Terminal SCMS sandbox

This repository orchestrates the container-terminal management sandbox. Application source remains in sibling repositories so MiniTOS, Terminal OES, and the simulator can evolve and release independently.

## Core stack

- MiniTOS: planning, inventory, terminal layout, routing, and operational master data.
- Terminal OES: released-order execution, CHE assignment, commands, and execution history.
- HiveMQ Community Edition: MQTT transport for the unified namespace.
- PostgreSQL: one authoritative database per application.

HiveMQ is the integration backbone, not a durable system of record. Each service owns its database and uses an inbox/outbox pattern for reliable at-least-once integration.

## Repository layout

Clone the repositories side by side:

```text
workspace/
  minitos/
  terminal-oes/
  godot-open-terminal/
  terminal-scms/
```

Then start the core stack:

```bash
cd terminal-scms
cp .env.example .env
docker compose config --quiet
docker compose up --build --detach
docker compose ps
```

Local endpoints:

- MiniTOS: http://localhost:8000/
- Terminal OES health: http://localhost:8010/health/
- HiveMQ MQTT: `localhost:1883`

Change every placeholder secret before exposing the stack outside a local development machine.

## Deferred profiles

FlowFuse and Timebase are deliberately separate deployments:

- FlowFuse self-hosted requires a reachable domain and wildcard DNS. Its platform broker is not a replacement for the SCMS HiveMQ UNS; Node-RED instances connect to HiveMQ as an application endpoint.
- Timebase 2.0 is currently beta and its official agent runbook requires interactive certificate, administrator-password, client-secret, and optional AI-provider steps.
- The current Godot scene begins with an interactive MiniTOS/MQTT connection UI. It remains a host-run simulator until a separate non-interactive runtime scene is implemented.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/FLOWFUSE.md](docs/FLOWFUSE.md), and [docs/TIMEBASE.md](docs/TIMEBASE.md).

