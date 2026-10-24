# Terminal simulator host runner

This small host-side process claims OES-created `SimulationSession` records and
launches only `terminal-scms/godot-simulator:local`. It uses the Docker CLI on
the host. The OES container never receives the Docker socket.

```bash
export OES_URL=http://127.0.0.1:8010
export SIMULATOR_RUNNER_TOKEN=replace-me
export RUNNER_IDENTITY=linux-server-01
python3 apps/simulator-runner/runner.py
```

The runner validates UUIDs and a fixed environment allowlist before creating a
container. OES business API credentials and MiniTOS credentials are never sent
to the simulator.
