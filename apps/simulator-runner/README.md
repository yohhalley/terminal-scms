# Terminal simulator runner

The runner claims OES-created `SimulationSession` records and starts the headless
Godot simulator for each one. It runs in one of two modes (`RUNNER_LAUNCH_MODE`):

- `process`: the `simulator-runner` compose service (below). Godot runs as a
  child process inside the runner container. Use this mode on the testing server.
- `docker` (default when run by hand): the host runner described further down.

## Compose service (`simulator-runner`, Runner S2)

The service is in the compose profile `simulation`, so `make up` does not start
it. The image (`Dockerfile` in this folder) holds Godot headless, the
`clients/godot-open-terminal` project and `runner.py`; it runs as an unprivileged
user and gets no Docker socket.

```bash
make up            # core stack: broker, MiniTOS, OES, ...
make up-runner     # build + start simulator-runner (profile "simulation")
make scenario-reset SIM=1   # reset import_v1, then start a fresh session
make logs-runner   # runner + Godot output
make sim-stop      # stop the session; the runner stops Godot
```

`make scenario-reset SIM=1` stops any active session first, runs the normal
reset (see `docs/SCENARIO_RESET.md`) and then calls `simulation_start`. OES
creates the session; the runner claims it within `RUNNER_POLL_INTERVAL` seconds
and Godot fetches its bootstrap from OES and connects to the broker that OES
names in the claim (`MQTT_HOST`/`MQTT_PORT` of the OES service). Godot plays
the scenario that the reset seeded.

Configuration comes only from the environment (`.env`, never committed;
`.env.example` holds placeholders):

| Variable | Used for |
| --- | --- |
| `SIMULATOR_RUNNER_TOKEN` | shared secret with OES (required) |
| `RUNNER_IDENTITY` | runner name stored on claimed sessions; keep it stable across restarts. A plain name, never an address or hostname |
| `SIMULATOR_OES_URL` | OES URL as seen from containers (default `http://terminal-oes:8000`) |
| `RUNNER_POLL_INTERVAL` | claim/status poll interval in seconds (default 2) |
| `RUNNER_LOG_LEVEL` | runner log level (default `INFO`) |
| `GODOT_VERSION`, `GODOT_PLATFORM`, `GODOT_CONTEXT` | image build only |

Inside the image the mode is fixed by `RUNNER_LAUNCH_MODE=process`,
`RUNNER_DATA_DIR=/data` (named volume `simulator-runner-data`),
`GODOT_BINARY` and `GODOT_PROJECT_PATH=/app`.

### Restarts

- Each claim is saved (mode 600, it holds the bootstrap token) under
  `/data/runner` before Godot starts; each session gets its own Godot user
  directory under `/data/sessions/<id>` (checkpoint, Sparkplug `bdSeq`).
- After a restart (`restart: unless-stopped`, a crash or a redeploy) the runner
  first settles every saved claim with OES and only then claims new sessions:
  - OES still serves the bootstrap (token valid, desired state running): Godot
    is started again for the same session and resumes from its checkpoint with
    a new `bdSeq`.
  - OES wants the session stopped: reported `stopped`.
  - The token has expired (OES `SIMULATION_BOOTSTRAP_TOKEN_TTL_SECONDS`, 300 s by
    default): reported `failed` ("Simulator runner restarted; the session could
    not be resumed ..."). Start a new session (`make sim-start`, or
    `make scenario-reset SIM=1` for a clean world). A session the user had
    already stopped is also reported `failed` in this case.
  - The session no longer exists (wiped by a reset): the saved claim is dropped.
- `docker compose stop` / redeploy sends SIGTERM: the runner stops Godot and
  reports `stopped` (grace period 30 s). The final status is retried a few times;
  if OES still cannot be reached the claim is kept and settled at the next start.

## Host runner (`docker` mode)

This small host-side process claims OES-created `SimulationSession` records and
launches only `terminal-scms/godot-simulator:local`. It uses the Docker CLI on
the host. The OES container never receives the Docker socket.

```bash
export OES_URL=http://127.0.0.1:8010
export SIMULATOR_RUNNER_TOKEN="$(openssl rand -hex 32)"
export RUNNER_IDENTITY=linux-server-01
python3 apps/simulator-runner/runner.py
```

OES must be started with the exact same `SIMULATOR_RUNNER_TOKEN`. In the normal
repository workflow, store the generated value in the ignored root `.env`,
recreate the OES services after changing it, and use
`make run-simulator-runner`; that target sources `.env` automatically. Never
commit the generated secret.

The runner validates UUIDs and a fixed environment allowlist before creating a
container. OES business API credentials and MiniTOS credentials are never sent
to the simulator.

Optional deterministic-simulation settings that OES may include in the claim
environment are allowlisted explicitly (see `ALLOWED_ENVIRONMENT`):
`SIMULATOR_SEED`, `SIMULATOR_RUN_ID`, `SIMULATOR_TIME_SCALE`,
`SIMULATOR_FIXED_STEP_MS`, `SIMULATOR_DEFAULT_SPEED_MPS`, `SIMULATOR_START_EPOCH_MS`, `SIMULATOR_DEVICE_ID_MODE`,
`SIMULATOR_QC_WAIT_MIN_S`, `SIMULATOR_QC_WAIT_MAX_S`, `SIMULATOR_QC_HOLD_WAIT_MIN_S`,
`SIMULATOR_QC_HOLD_WAIT_MAX_S`, `SIMULATOR_RTG_WAIT_MIN_S`, `SIMULATOR_RTG_WAIT_MAX_S`,
`SIMULATOR_RTG_HOLD_WAIT_MIN_S`, `SIMULATOR_RTG_HOLD_WAIT_MAX_S`, `SIMULATOR_TT_NO_SHOW_TIMEOUT_S`,
`SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED` (default `false`: an RTG waiting for its TT
never times out; `true` applies `SIMULATOR_TT_NO_SHOW_TIMEOUT_S` to the RTG too, pending O7),
`SIMULATOR_COMMAND_TTL_S` and `SIMULATOR_SCENARIO_ID` (NBIRTH `Sim/ScenarioId`;
the OES bootstrap `session.scenario_id` wins when present).

`SIMULATOR_DEVICE_ID_MODE` defaults to `name` in the simulator (device id =
MiniTOS `Che.name`, e.g. `QC01`, as the contract and OES expect); set it to
`legacy` only for the old `CHE_{id}` ids.

Run the tests (unittest or pytest; no Docker needed):

```bash
cd apps/simulator-runner && python3 -m unittest -v test_runner
```
