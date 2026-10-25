# Terminal simulator host runner

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
`SIMULATOR_RTG_HOLD_WAIT_MIN_S`, `SIMULATOR_RTG_HOLD_WAIT_MAX_S`, `SIMULATOR_TT_NO_SHOW_TIMEOUT_S` and
`SIMULATOR_COMMAND_TTL_S`.

`SIMULATOR_DEVICE_ID_MODE` defaults to `name` in the simulator (device id =
MiniTOS `Che.name`, e.g. `QC01`, as the contract and OES expect); set it to
`legacy` only for the old `CHE_{id}` ids.

Run the tests (unittest or pytest):

```bash
cd apps/simulator-runner && python3 -m unittest -v test_runner
```
