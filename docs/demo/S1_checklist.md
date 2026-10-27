# S1 demo run checklist: import scenario, steps 1–3 (+4)

Prepared by QA (TCL-001) on 27 Oct 2026, for the S1 sprint review. v2 addresses the Tech Lead and SME reviews of PR #19.
- **Scenario spec:** `docs/scenarios/import_v1.md`, with acceptance checks AC*.
- **Evidence:** `main` @ `9c4b96d` plus the open PRs below (tips as read on 27 Oct). QA has **no Docker**, so every command below is
  **UNVERIFIED** unless it's marked ✔ (read in the code, Makefile or README, not executed). Run it once, end to end,
  on Yael's machine before the review.
- **Q18** (used below): the contract rule that the TT→RTG hand-off is dispatched only after the QC→TT hand-off has transferred (`apps/terminal-oes/docs/UNS_CONTRACT.md` § Hand-offs).

## 0. Merge list: needed before the demo can pass

- [ ] PR #13 TCL-003a (OES hand-off dispatch + MiniTOS custody) is merged into `main`. It also passes the `SIMULATOR_*` settings in the runner claim ✔.
- [ ] PR #14 TCL-005 (contract tests; based on #13) is merged.
- [ ] PR #15 TCL-014a (Godot QC→TT) is merged.
- [ ] PR #16 TCL-029 (Godot CI; adds `test_headless_sim.gd` to `make test-godot`) is merged. The sprint's "verified by" needs it.
- [ ] PR #18 TCL-004 (replaces the LAN address in `compose.yaml` with `${SCMS_SERVER_HOST:-localhost}`) is merged.
- [ ] PR #20 TCL-028 (dispatch-next response codes, `failure_detail` on `leg_failed`; based on #13) is merged. Recommended.
- [ ] PR #17 TCL-014b (TT→RTG) is merged, if step 4 is shown. It's pulled forward from S2.
- [ ] TCL-006 (seed + `make scenario-reset SCENARIO=import_v1` + the release step command) is pushed. **It's not on origin today**, and `main`'s Makefile has no `scenario-reset` target.

## 1. Prerequisites

- [ ] Docker Engine + Compose v2. `make`, `python3`, `curl` and the `mosquitto-clients` package (for `mosquitto_sub`) on the host.
- [ ] `.env`: `make setup` copies `.env.example` → `.env` if it's missing ✔.
  - Set `SIMULATOR_RUNNER_TOKEN` (required by compose) and `OES_API_TOKEN` ✔. Never commit `.env`.
  - With #18 merged: set `SCMS_SERVER_HOST` in your local `.env` only if you browse from another machine; otherwise there is nothing to do. Don't copy the address into notes or slides.
- [ ] Load `.env` into each shell you use for the commands below, so the token is never typed: `set -a; . ./.env; set +a`.
- [ ] With #13 merged, compose sets `SIMULATOR_SEED=1`, `SIMULATOR_START_EPOCH_MS=1792886400000`, `EQUIPMENT_COMMAND_TTL_SIM_SECONDS=600` and `SIMULATOR_DEVICE_ID_MODE=name` ✔.
- [ ] Free ports (defaults from `.env.example`) ✔:

| Service | URL / port | Health |
|---|---|---|
| HiveMQ (MQTT) | `localhost:${MQTT_PORT:-1883}` | `make logs-mqtt` |
| MiniTOS | `http://localhost:${MINITOS_HTTP_PORT:-8000}/` (admin `/admin/`) | page loads |
| OES | `http://localhost:${OES_HTTP_PORT:-8010}/equipment/`, `/simulations/`; API `/api/` needs `X-OES-Token` | `/health/` → 200 |
| Workers | `minitos-uns-consumer`, `minitos-outbox`, `oes-uns-consumer`, `oes-outbox` | `make ps`, `make diagnose` |
| Godot | `godot-simulator` (profile `simulation`), started by the runner | runner log |

## 2. Bring-up (UNVERIFIED)

1. [ ] `make setup`: builds the stack, waits for health, then runs `ps` ✔.
2. [ ] `make create-minitos-admin` and `make create-oes-admin` (interactive) ✔.
3. [ ] `make ps`: every service is `running`/`healthy`. If not, run `make diagnose`.
4. [ ] `make scenario-reset SCENARIO=import_v1` (**TCL-006, missing**). Expect the known state (spec §1, K1–K7):
   - MiniTOS has 1 quay, `BLOCK01`, roads, `QC01`, `TT01`, `TT02`, `RTG01`, 1 vessel visit and 1 import container `MSCU1234566`, inbound, advised on the vessel.
   - In OES, the Site projection is `ready`.
5. [ ] In a separate terminal: `mosquitto_sub -h localhost -p ${MQTT_PORT:-1883} -t 'scms/v2/#' -v` (business events). Optionally,
   in a second terminal: `mosquitto_sub -h localhost -p ${MQTT_PORT:-1883} -t 'spBv1.0/#' -v`. Sparkplug payloads are protobuf, so expect unreadable bytes.
6. [ ] `make run-simulator-runner` (on the host: sources `.env`, builds the Godot image) ✔.
7. [ ] At `http://localhost:${OES_HTTP_PORT:-8010}/simulations/`, start the Site session. Pass: the session is `running`, and `/equipment/` lists the 4 CHEs as connected and available (AC2.3).

## 3. Walk-through with pass criteria

Commands for 3a/3b/4 (✔ paths and header checked in the code; not executed):
- **Release (3a):** any logged-in MiniTOS user (e.g. the superuser from step 2). Browsable API after logging in at `/admin/`, or
  `curl -u <admin> -X POST "http://localhost:${MINITOS_HTTP_PORT:-8000}/minitos_core/locations/<loc>/work_queues/<wq>/orders/<id>/release/"`.
  `<loc>`, `<wq>` and `<id>` (the MiniTOS order id) are shown in the admin `Order` list. Or use the TCL-006 step command once it exists.
- **Find the OES order id:** `curl -s -H "X-OES-Token: $OES_API_TOKEN" "http://localhost:${OES_HTTP_PORT:-8010}/api/orders/?status=released"`.
  Use its `id`. That is **not** the MiniTOS order id, which appears as `tos_order_id`.
- **Dispatch (3b, 4):** `curl -s -X POST -H "X-OES-Token: $OES_API_TOKEN" -H 'Content-Type: application/json' -d '{}' "http://localhost:${OES_HTTP_PORT:-8010}/api/orders/<oes_id>/dispatch-next/"`.
  It returns 201 when commands are sent. Without the header it returns 403.

| # | Action | Pass criteria (see the spec for the full AC list) |
|---|---|---|
| 1 Inbound, advised on vessel | Show the seeded container visit in MiniTOS admin → Container visits | Status `Preadvised` (read as "inbound, advised on vessel"), category Import, inbound carrier = the vessel visit, no CHE, no slot (AC1.1–1.2). *No UNS event exists yet (spec G1/Q2): say so.* |
| 2 Vessel arrival (optional) | Not in the S1 script. Optionally show the seeded vessel visit state in admin; it's a check, not an action | Matches the seed. Don't use the transition API: it's broken (bug TCL-032). |
| 3a Release | Release command above | One message on `…/tos/orders/{id}/released`. The OES order is `released` with 3 planned operations (AC3.1). |
| 3b Dispatch | Dispatch command above, once | Two `leg_dispatched` (QC01, TTxx). `/equipment/` shows QC and TT dispatched. Calling it again now sends **no new command**: 400 with the Q18 message on #13, 200 with `dispatched: false` after #20 (AC3.2). |
| 3c Appear | (Godot, automatic) | `leg_started` (QC01), then `container_picked_up` with `source: VESSEL`. Admin shows "On Equipment", current CHE QC01 (AC3.4–3.5). |
| 3d Transfer | (Godot) Wait for the seeded 10–30 s hold (**compressed time**, not a real crane cycle) plus the TT's drive time | `leg_started` (TT), then exactly one `container_transferred` QC01→TT, then `leg_completed` (QC01). Admin shows "On Equipment" with current CHE = TT (AC3.4–3.6). |
| 4 (stretch, needs #17) | Dispatch command again, after 3d | `leg_dispatched`/`leg_started` RTG01, `container_transferred` TT→RTG01, `leg_completed` TT, `container_dropped` (slot), `leg_completed` RTG01. Admin shows `At Yard` in the planned slot; the order is `complete` in both systems (AC4.1–4.4). **No timeout in S1** while the TT waits at the block: if it stalls at `waiting_for_handoff`, stop the session and reset. |
| Rerun | `make scenario-reset SCENARIO=import_v1`, then repeat 3a–3d | The same states in the same order, the same TT chosen, the same hold (D-016, AC4.6). |

This order matches the S1 script: the first dispatch sends QC and TT together, and the second sends the TT→RTG hand-off.

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
- Godot runs headless: there's no 3D view. The evidence is UNS messages, admin pages and the OES console. Whether the demo should show Godot visually is a Fri 30 Oct question.
- QC and RTG holds are compressed time (10–30 s); don't quote rates from them.
- The TT no-show timeout is configurable (D-017: NBIRTH, then `SIMULATOR_TT_NO_SHOW_TIMEOUT_S`, then 300 s). The no-show path is a simulator-failure marker, not an operational recovery.
- Steps 5–9 (dwell, truck, gate) aren't in S1 (T-005, S3).
- The OES console shows raw telemetry; there's no visit timeline yet (TCL-021, S4).
- S2 follow-up: rename the released-payload `container_number` (visit pk) to `container_visit_id` (logged by the SME).
