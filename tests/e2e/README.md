# tests/e2e: CI end-to-end slice (TCL-007)

`s1_slice.py` drives the S1 demo slice of `import_v1` (`docs/demo/S1_checklist.md`,
`docs/scenarios/import_v1.md`) against a live stack. It asserts the `scms/v2/...` UNS
events in strict order and enforces the per-step time limit (D-037). The workflow is
`.github/workflows/e2e.yml` (job `s1-slice`).

## What the job does

1. Build the images (`--profile simulation`) and bring the core stack up with `--wait`.
   The `godot-simulator` service is **not** started with `up`. Each session needs its own
   environment (session id, token, seed, time scale), so the host-side runner
   (`apps/simulator-runner/runner.py`) claims the session and launches the image with
   `docker run` on the `terminal-scms` network, just as `make run-simulator-runner` does.
2. Reset to the seeded state with `make scenario-reset SCENARIO=import_v1`. Then move the
   visit to InOperation with `make scenario-step SCENARIO=import_v1 STEP=visit-in-operation`,
   which creates the discharge orders (TCL-010, #34). This step is untimed setup. Until #34
   is on main it fails as an unknown step.
3. Run `python tests/e2e/s1_slice.py`. It does an untimed setup (health, Site ready,
   start-simulation, session `running`, QC01/TT01/TT02/RTG01 connected and available),
   then runs steps 3a, 3b, 3c, 3d and 4, then the end-state checks, then stops the session.
4. Always upload `e2e-report/` (`s1_slice.md`, `s1_slice.json`, `uns_messages.jsonl`).
   On failure, also upload the container, runner and simulator logs. Then tear down with `down --volumes`.

The exit code is 0 only when every step passes.

## Timing rule (D-037)

- A step's clock starts when its trigger is published and stops when the step's last
  expected event is received from the broker.
  - Release (3a): just before the MiniTOS release command is issued. The CLI's own run
    time is inside the clock and is reported as "Trigger (s)".
  - Dispatch steps (3b, 4): just before the OES `dispatch-next` request is sent.
  - Simulator-driven steps (3c, 3d): the exact time the previous step's last event was
    received. Simulator envelope times are sim time, not wall time.
- Every event's arrival time is taken in the MQTT thread, so the time the runner spends
  reading never counts. The report's "Clock from" column names the start point.
- The limit is 5 s per step (`E2E_STEP_LIMIT_S`). An event that arrives after its deadline fails the step.
- A flaky or slow run gets a ticket, with the uploaded report and logs attached.
  **Never raise the limit to make a run green.**

## Fast timings (interim)

There is no simulator timing-profile parameter yet (S2 "Sim params").
Until there is one, two things stand in for it:

- the session `time_scale` (`E2E_TIME_SCALE`, default 10);
- `tests/e2e/compose.e2e.yaml`, which sets `SIMULATOR_QC_WAIT_MIN_S=1` and `SIMULATOR_QC_WAIT_MAX_S=2` on terminal-oes.

`SIMULATOR_RTG_*` waits are not forwarded by OES today, so the RTG hold keeps its default.

## Local use

```sh
pip install -r tests/e2e/requirements.txt
python -m unittest discover -s tests/e2e -p 'test_*.py'      # self-test, no stack needed
export COMPOSE_FILE=compose.yaml:tests/e2e/compose.e2e.yaml
make scenario-reset SCENARIO=import_v1                       # stack up + runner running
make scenario-step SCENARIO=import_v1 STEP=visit-in-operation # TCL-010: creates the orders
OES_API_TOKEN=... python tests/e2e/s1_slice.py --report-dir e2e-report
```

Options:

- `--no-step4` stops after the first hand-off.
- `--keep-simulation` leaves the session running.
- `--compare-with <earlier s1_slice.json>` checks the D-016 rerun after another reset.

Tokens come only from the environment. The runner never reads `.env`.

## Status

The job is not a required check. It stays allowed to fail until it has been green on
main 3 runs in a row.

## Known blockers

- **M2 reset fix (FE).** After a reset, a stale message from the previous run can still be
  accepted. The rerun pass (`E2E_RERUN=1`, reset, run, compare) stays off until M2 merges.
- **TCL-010 (#34, stacked on #27).** `visit-in-operation` exists only once #34 lands.
- **TCL-034 (sim params).** A real timing profile, and OES forwarding `SIMULATOR_RTG_*`.
- **CHEs never become available after a reset.** OES `site_sync` imports every CHE with
  `powered_on=False` and `is_available=False`, even though MiniTOS seeds them powered on.
  The simulator bootstrap then applies that OES state. Setup times out at "connected and
  available" until this is fixed (bug, Archer).
