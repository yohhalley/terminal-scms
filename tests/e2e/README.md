# tests/e2e: CI end-to-end slice (TCL-007)

`s1_slice.py` drives the S1 demo slice of `import_v1` (`docs/demo/S1_checklist.md`,
`docs/scenarios/import_v1.md`) against a live stack. It asserts the `scms/v2/...` UNS
events in strict order and enforces the per-step time limit (D-037). The workflow is
`.github/workflows/e2e.yml` (job `s1-slice`).

Only `scms.order.released.v1` and `scms.execution.event.v1` for the slice's order are
in the strict order. Visit `scms.visit.status_changed.v1` events (TCL-003b, #37: one per
seeded visit at reset, one at visit-in-operation, container-visit changes during
execution) are recorded in `uns_messages.jsonl` but are neither matched nor "extra".

## What the job does

1. Build the core images and the `simulator-runner` image (`--profile simulation`).
   The official Godot import (#29, #35) runs at image build as the `godot` user
   (`.github/scripts/godot-import.sh`). The standalone `godot-simulator` service is
   not built or started. Bring the core stack up with `--wait`, then start the
   `simulator-runner` compose service, which claims the OES session and runs
   headless Godot as a child process (no Docker socket, no host-side runner).
2. Reset to the seeded state with `make scenario-reset SCENARIO=import_v1`. Then move the
   visit to InOperation with `make scenario-step SCENARIO=import_v1 STEP=visit-in-operation`,
   which creates (or adopts) the discharge orders (TCL-010). This step is untimed setup.
3. Run `python tests/e2e/s1_slice.py`. It does an untimed setup (health, Site ready,
   start-simulation, session `running`, QC01/TT01/TT02/RTG01 connected and available),
   then runs steps 3a, 3b, 3c, 3d and 4, then the end-state checks, then stops the session.
4. Reset again, repeat visit-in-operation, and rerun the slice with `--compare-with`
   (D-016). M2 is on main, so this pass is on (`E2E_RERUN=1`).
5. Always upload `e2e-report/` (`s1_slice.md`, `s1_slice.json`, `uns_messages.jsonl`,
   and the rerun copies). On failure, also upload the container logs. Then tear down
   with `down --volumes`.

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

## Fast timings (TCL-034)

CI and this job set `SIMULATOR_TIMING_PROFILE=fast` in `.env` so OES forwards it on
the session claim (D-039). The fast profile's crane holds are 2..5 sim seconds (an
assumption, Q22). The live demo is ×1 (D-040). CI speed is still open (Q24). This job uses
`E2E_TIME_SCALE=10` so the fast profile's 2..5 sim-second holds fit the 5 s
wall-clock limit (D-037). A run at ×1 (43690094196) failed step 3d at 5.0 s:
`leg_started(leg 2, TT02)` was seen, then `container_transferred` was not (the
QC hold is still running). **The 5 s limit was not raised.** There is no
`compose.e2e.yaml` override; the interim QC-wait knobs were replaced by this profile.

## Local use

```sh
pip install -r tests/e2e/requirements.txt
python -m unittest discover -s tests/e2e -p 'test_*.py'      # self-test, no stack needed
# .env: SIMULATOR_TIMING_PROFILE=fast
docker compose up --detach --wait
docker compose --profile simulation up --detach --wait simulator-runner
make scenario-reset SCENARIO=import_v1
make scenario-step SCENARIO=import_v1 STEP=visit-in-operation # TCL-010: creates/adopts the orders
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
