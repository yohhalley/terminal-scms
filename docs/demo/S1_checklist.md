# S1 demo run checklist: import scenario, steps 1–3 (+4)

Prepared by QA (TCL-001) on 27 Oct 2026, for the S1 sprint review.
- **Scenario spec:** `docs/scenarios/import_v1.md`, with acceptance checks AC*.
- **Evidence:** `main` @ `9c4b96d` plus the open PRs listed below. QA has **no Docker**, so every command below is
  **UNVERIFIED** unless it's marked ✔ (read in the Makefile or README, not executed). Run it once, end to end,
  on Yael's machine before the review.

## 0. Blockers: these must be merged or done before the demo can pass

- [ ] PR #13 TCL-003a (OES hand-off dispatch + MiniTOS custody) is merged into `main`.
- [ ] PR #14 TCL-005 (contract tests; based on #13) is merged.
- [ ] PR #15 TCL-014a (Godot QC→TT) is merged. Known open point: the OES claim doesn't pass `SIMULATOR_*` yet (NOTES-TCL-014a).
- [ ] PR #17 TCL-014b (TT→RTG) is merged, if step 4 is shown. It's pulled forward from S2.
- [ ] TCL-006 (seed + `make scenario-reset SCENARIO=import_v1` + the release step command) is pushed. **It's not on origin today**, and `main`'s Makefile has no `scenario-reset` target.
- [ ] Decide how the vessel visit gets to `Arrived`: the API `transition-status` is expected to fail with a 500 (spec gap G2), and the admin panel is TCL-009 (S2). Spec Q3.

## 1. Prerequisites

- [ ] Docker Engine + Compose v2. `make`, `python3` and the `mosquitto-clients` package (for `mosquitto_sub`) on the host.
- [ ] `.env`: `make setup` copies `.env.example` → `.env` if it's missing ✔.
  - Set `SIMULATOR_RUNNER_TOKEN` (required by compose) ✔.
  - **Remove any LAN address** from `MINITOS_ALLOWED_HOSTS` / `OES_ALLOWED_HOSTS` and from `compose.yaml` overrides. Use `localhost` only. `main` currently ships a LAN address in both; don't copy it into notes or slides.
  - Never commit `.env`.
- [ ] With #13 merged, compose sets `SIMULATOR_SEED=1`, `SIMULATOR_START_EPOCH_MS=1792886400000`, `EQUIPMENT_COMMAND_TTL_SIM_SECONDS=600` and `SIMULATOR_DEVICE_ID_MODE=name` ✔ (read).
- [ ] Free ports (defaults from `.env.example`) ✔:

| Service | URL / port | Health |
|---|---|---|
| HiveMQ (MQTT) | `localhost:${MQTT_PORT:-1883}` | `make logs-mqtt` |
| MiniTOS | `http://localhost:${MINITOS_HTTP_PORT:-8000}/` (admin `/admin/`) | page loads |
| OES | `http://localhost:${OES_HTTP_PORT:-8010}/equipment/`, `/simulations/` | `/health/` → 200 |
| Workers | `minitos-uns-consumer`, `minitos-outbox`, `oes-uns-consumer`, `oes-outbox` | `make ps`, `make diagnose` |
| Godot | `godot-simulator` (profile `simulation`), started by the runner | runner log |

## 2. Bring-up (UNVERIFIED)

1. [ ] `make setup`: builds the stack, waits for health, then runs `ps` ✔.
2. [ ] `make create-minitos-admin` and `make create-oes-admin` (interactive) ✔.
3. [ ] `make ps`: every service is `running`/`healthy`. If not, run `make diagnose`.
4. [ ] `make scenario-reset SCENARIO=import_v1` (**TCL-006, missing**). Expect a clean known state (spec §1, K1–K7):
   - MiniTOS has 1 quay, `BLOCK01`, roads, `QC01`, `TT01`, `TT02`, `RTG01`, 1 vessel visit and 1 import container.
   - In OES, the Site projection is `ready`.
5. [ ] In a separate terminal: `mosquitto_sub -h localhost -p 1883 -t 'scms/v2/#' -v` (business events). Optionally,
   in a second terminal: `mosquitto_sub -h localhost -p 1883 -t 'spBv1.0/#' -v`. The Sparkplug payloads are protobuf, so expect unreadable bytes.
6. [ ] `make run-simulator-runner` (on the host: sources `.env`, builds the Godot image) ✔.
7. [ ] At `http://localhost:8010/simulations/`, start the Site session. Pass: the session is `running`, and `/equipment/` lists the 4 CHEs as connected and available (AC2.3).

## 3. Walk-through with pass criteria

| # | Action | Pass criteria (see the spec for the full AC list) |
|---|---|---|
| 1 Pre-advice | Show the seeded container visit in MiniTOS admin → Container visits | `Preadvised`, category Import, inbound carrier = the vessel visit, no CHE, no slot (AC1.1–1.2). *No UNS event exists yet (G1/Q2): say so.* |
| 2 Vessel arrival | Set the vessel visit to `Arrived` (method per the last blocker in §0) | Admin shows `Arrived` (AC2.1). *No UNS event (G1).* The container status is per Q4. |
| 3a Release | Release the discharge order (TCL-006 step command, or MiniTOS `POST …/orders/{id}/release/`) | One message on `…/tos/orders/{id}/released`. The OES order `released` with 3 planned operations (AC3.1). |
| 3b Dispatch | OES `POST /api/orders/{id}/dispatch-next/` (once) | Two `leg_dispatched` (QC01, TTxx). `/equipment/` shows QC and TT dispatched. A second call now is refused, "Q18" (AC3.2). |
| 3c Appear | (Godot, automatic) | `leg_started` (QC01), then `container_picked_up` with `source: VESSEL`. Admin shows "On Equipment", current CHE QC01 (AC3.4–3.5). |
| 3d Transfer | (Godot) Wait for the 10–30 s seeded hold, plus the TT's drive time | `leg_started` (TT), then exactly one `container_transferred` QC01→TT, then `leg_completed` (QC01). Admin shows "On Equipment" with current CHE = TT (AC3.4–3.6). |
| 4 (stretch, needs #17) | OES `dispatch-next` again | `leg_dispatched`/`leg_started` RTG01, `container_transferred` TT→RTG01, `leg_completed` TT, `container_dropped` (slot), `leg_completed` RTG01. Admin shows `At Yard` in the planned slot; the order is `complete` in both systems (AC4.1–4.4). |
| Rerun | `make scenario-reset SCENARIO=import_v1`, then repeat 3a–3d | The same states in the same order, the same TT chosen, the same hold (D-016, AC4.6). |

Note: the S1 sprint note's demo script says "dispatch twice (TT to quay, then QC place-on-TT)". In the code (#13), the **first** call dispatches QC and TT together (T-006), and the **second** call dispatches the TT→RTG hand-off (step 4). Use the table above, and the Tech Lead should update the script.

General pass rules for the run:
- No `rejected` command-result receipts in OES.
- No `failed` inbox rows in either DB.
- `make diagnose` shows no worker tracebacks.

## 4. Automated tests to run before the demo (UNVERIFIED in compose)

| Target | What it runs ✔ | Relevant to |
|---|---|---|
| `make test-minitos` | `manage.py test --settings=minitos_site.settings_test` | custody, D-015, release payload (after #13/#14) |
| `make test-oes` | `manage.py test` | dispatch, hand-offs, D-014/016/017, contracts (after #13/#14) |
| `make test-runner` | `unittest` for `apps/simulator-runner` | session claim/runner |
| `make test-godot` | `test_terminal_integration.gd` + `test_che_rails.gd` only on `main`; **`test_headless_sim.gd` is only added by TCL-029 (#16)** | QC/TT state machine, seed replay |
| `make test` | all of the above | — |

For reference: on 27 Oct QA ran only the OES hand-off happy-path scenario from #13, outside Docker (SQLite, at the TCL-005 tip). The full suites weren't run for this checklist, and that run is no substitute for compose.

## 5. Caveats to state in the review

- KG and historian aren't shown: they're decided on Fri 30 Oct (D-004). The spec's KG/historian columns are TBD.
- Godot runs headless: there's no 3D view. The evidence is UNS messages, admin pages and the OES console.
- Steps 5–9 (dwell, truck, gate) aren't in S1 (T-005, S3).
- The OES console shows raw telemetry; there's no visit timeline yet (TCL-021, S4).
