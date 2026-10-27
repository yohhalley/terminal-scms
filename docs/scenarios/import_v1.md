# Import scenario `import_v1`: acceptance spec (TCL-001)

- **Scenario:** import container visit, issue [#1](https://github.com/yohhalley/terminal-scms/issues/1). The export visit (#2) is out of scope here (D-002: import first).
- **Owner:** QA (Moon Unit). **Reviewers:** SME (TCL-015) and Tech Lead.
- **Status:** draft v2, 27 Oct 2026. v2 addresses the Tech Lead's and SME's reviews of PR #19. Steps 1–4 are complete. Steps 5–9 are an outline. The KG and historian columns stay `TBD (Yael decides Fri)` until Yael decides on Fri 30 Oct (D-004, TCL-013).
- **Code base read** (branch tips **as read on 27 Oct 2026**; they will move): `main` @ `9c4b96d`, plus the open PRs this scenario depends on:
  - #13 TCL-003a (OES hand-off dispatch), branch `TCL-003a-handoff-dispatch` @ `baf3adf`
  - #14 TCL-005 (contract tests) @ `9785428`
  - #15 TCL-014a (Godot QC→TT) @ `4dec5a1`
  - #16 TCL-029 (Godot CI) @ `0bf7d42`
  - #17 TCL-014b (Godot TT→RTG) @ `b3dded5`
  - #18 TCL-004 (LAN IP placeholder) @ `0c1d561`
  - #20 TCL-028 (dispatch-next API, based on #13) @ `5e36047`

  A claim marked **[PR #n]** is true only on that branch, not on `main`.
- **Decisions:** D-0xx and T-0xx refer to the team vault note `03 Decisions.md` (Obsidian). That vault is not in this repo.
- **Evidence key:**
  - **[code]**: read in the file named.
  - **[test]**: an existing automated test asserts it.
  - **[run]**: QA ran it on 27 Oct 2026 (SQLite, Python 3.13, no Docker).
  - **[inferred]**: read from code but not executed.
  - **[TBD]**: not decided or not built.
- **Terms from team notes:**
  - **Q18:** the contract rule that the TT→RTG hand-off (H2) is dispatched only after the QC→TT hand-off (H1) has transferred (`apps/terminal-oes/docs/UNS_CONTRACT.md` § Hand-offs, cancel and sim-time expiry [PR #13]).
  - **O6–O12:** open points of the contract proposal rev 9.3 (team notes); the ones relevant here are restated where used.
  - **A2, B3, D012-x, D016-x:** gap and test-case ids from QA's internal survey (team notes, not in the repo); each is glossed where used.
  - **Compressed time:** the QC and RTG holds (10–30 s, D-012) are far shorter than real cycles (QC ~90–120 s, RTG ~60–120 s, SME review). Don't read productivity rates from them.

## 0. Conventions

| Item | Value | Source |
|---|---|---|
| Business topic root | `scms/v2/{enterprise_code}/{site_code}/…`, QoS 1, not retained, `event_id` UUID, transactional outbox + idempotent inbox | `apps/terminal-oes/docs/UNS_CONTRACT.md` |
| MiniTOS → OES | `…/tos/orders/{order_id}/released`, schema `scms.order.released.v1` | `apps/minitos/minitos_core/services/order_execution.py::_queue_released_order` |
| OES → MiniTOS | `…/oes/orders/{order_id}/execution`, schema `scms.execution.event.v1` | `apps/terminal-oes/execution/services/events.py::queue_execution_event`; JSON Schema `contracts/schemas/scms.execution.event.v1.schema.json` [PR #14] |
| Sparkplug | `spBv1.0/{group}/{NBIRTH,DBIRTH,DDATA,DCMD,NCMD,NDEATH,DDEATH}/{node}[/{device}]`, plus `spBv1.0/STATE/{host_id}` | `UNS_CONTRACT.md` § Sparkplug runtime boundary |
| Device id | the MiniTOS CHE name (`QC01`, `TT01`, `RTG01`), set by `SIMULATOR_DEVICE_ID_MODE=name` | `compose.yaml` [PR #13]; `clients/godot-open-terminal/NOTES-TCL-014a.md` [PR #15] |
| Location on a CHE | status `OnEquipment` ("On Equipment") + `current_che` / `current_che_name` (D-015) | `minitos_core/models.py::ContainerVisit` |
| Time | Event times come from sim time (`Event/TimeMs = Sim/StartEpochMs + Sim/TimeMs`). The default `SIMULATOR_START_EPOCH_MS=1792886400000` is 2026-10-25T00:00Z. | `compose.yaml` [PR #13]; NOTES-TCL-014a |
| Rerun | the same business states in the same order; payload bytes may differ (D-016) | D-016 |

## 1. Seeded known state (precondition for step 1)

There is no seed or reset yet. It is TCL-006 (FE), which is not on origin. The S1 sprint note sets the target: 1 quay, 1 block, roads, 1 QC, 2 TT, 1 RTG, and 1 vessel visit with 1 pre-advised container (room for 5). It also requires deterministic UUIDs, a seeded RNG, and `make scenario-reset SCENARIO=import_v1`, which resets both DBs, clears retained MQTT and publishes the Site. Until TCL-006 lands, every check below assumes this state:

| ID | State | Where it lives |
|---|---|---|
| K1 | Enterprise + Site published: OES `SiteProjection.projection_status = ready`, revision N, checksum matches | MiniTOS `site_sync.py` → `tos/site/created`, `tos/configuration/published`; OES `execution/services/site_sync.py` |
| K2 | Layout with quay/berth, block `BLOCK01` with free ground slots, transfer lane(s), roads | MiniTOS `Position`, `OperationalPosition`, `StorageGrid` (`BLOCK01` is the exception block in `order_execution.py::EXCEPTION_BLOCK_NAME`) [PR #13] |
| K3 | CHEs `QC01` (type STS/QC), `TT01`, `TT02`, `RTG01`, all available and not dispatched | MiniTOS `Che`; OES `EquipmentState` after DBIRTH |
| K4 | One vessel `CarrierVisit` (`visit_type='Vessel'`, status `Created`) | `models.py::CarrierVisit` |
| K5 | One `ContainerUnit` with a valid ISO 6346 number, e.g. `MSCU1234566` (check digit 6, computed by QA), `is_in_terminal=False`, and its import `ContainerVisit` **seeded** as inbound, advised on the vessel (S1 sprint plan and TCL-006 acceptance; see Q1). Note: the existing test fixtures and goldens use `MSCU1234565`, whose check digit is wrong (follow-up F2). | `models.py::ContainerUnit` |
| K6 | No orders, commands or hand-offs in either DB. MQTT retained topics are cleared except `spBv1.0/STATE/{host}`. | — |
| K7 | Seed values: `SIMULATOR_SEED=1`, `SIMULATOR_START_EPOCH_MS=1792886400000`, QC hold 10..30 s (compressed time), TT no-show timeout configurable per D-017: NBIRTH `Sim/Timing/TtNoShowTimeoutS`, else `SIMULATOR_TT_NO_SHOW_TIMEOUT_S`, else 300 s | `compose.yaml` [PR #13], NOTES-TCL-014a |

## 2. Step summary

Status of each step today: ✅ built and tested, 🟡 partly built or only on an open PR, ❌ not built.

| # | Issue #1 step | Trigger | UNS event(s) | MiniTOS end state | OES end state | UI | KG | Historian | Built | Tests |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Pre-advice (inbound, advised on vessel) | Seed (S1) or MiniTOS user creates the container visit | ❌ none today | `ContainerVisit.status=Preadvised` | none | admin / DRF API | TBD (Yael decides Fri) | TBD (Yael decides Fri) | 🟡 MiniTOS only | ❌ |
| 2 | Vessel arrival (not in the S1 demo; optional check of the seeded state) | Vessel visit → `Arrived` (API broken: TCL-032) | ❌ none today | `CarrierVisit.status=Arrived` | Sim session running, CHEs born | admin; OES `/simulations/`, `/equipment/` | TBD (Yael decides Fri) | TBD (Yael decides Fri) | 🟡 transition API broken | ❌ (Sparkplug birth ✅) |
| 3 | Discharge QC→TT | Order released; OES `dispatch-next` (token) | `order.released`; `leg_dispatched`×2, `leg_started`, `container_picked_up`(VESSEL), `container_transferred` QC→TT, `leg_completed` | `OnEquipment`/QC01, then `OnEquipment`/TTxx | order `in_progress`, hand-off `transferred` | admin; OES `/equipment/`, `/api/orders/` | TBD (Yael decides Fri) | TBD (Yael decides Fri) | 🟡 PR #13, #15 | 🟡 per service |
| 4 | TT to yard, RTG stacks | 2nd `dispatch-next` (after H1, Q18) | `leg_dispatched`, `leg_started`, `container_transferred` TT→RTG, `leg_completed`, `container_dropped`, `leg_completed` | `AtYard` in slot; order `complete` | order `complete`, all CHEs free | admin | TBD (Yael decides Fri) | TBD (Yael decides Fri) | 🟡 PR #13, #17 | 🟡 per service |
| 5 | Yard dwell | time passes | TBD | `AtYard` unchanged | none | TBD | TBD (Yael decides Fri) | TBD (Yael decides Fri) | ❌ | ❌ |
| 6 | Truck booked | TBD (T-005 scripted driver) | TBD | truck `CarrierVisit` + delivery order TBD | TBD | TBD | TBD (Yael decides Fri) | TBD (Yael decides Fri) | ❌ | ❌ |
| 7 | Truck arrival | scripted gate-in (T-005) | TBD | truck visit `Arrived` TBD | TBD | TBD | TBD (Yael decides Fri) | TBD (Yael decides Fri) | ❌ | ❌ |
| 8 | Departure: RTG loads truck | OES dispatch of yard→truck leg | `container_picked_up` (yard), then TBD | `OnEquipment`/RTG01 → on truck TBD | TBD | TBD | TBD (Yael decides Fri) | TBD (Yael decides Fri) | ❌ | ❌ |
| 9 | Visit closure | scripted gate-out (T-005) | TBD | `Departed`/`Completed` TBD | TBD | visit history (TCL-021) | TBD (Yael decides Fri) | TBD (Yael decides Fri) | ❌ | ❌ |

## 3. Steps 1–4 (complete)

### Step 1: Pre-advice. The container is inbound, advised on the vessel

Wording (SME review): for an import, terminals say the container is **inbound, advised on vessel**, learned from the line's discharge list. "Pre-advice" is export/truck vocabulary. The enum value `Preadvised` stays; show it to users as "Inbound (advised on vessel V001)".

**Preconditions:** K1–K7. In S1 the visit is seeded by TCL-006, so step 1 is a check, not an action (Q1).

| Aspect | Expected | Evidence |
|---|---|---|
| Trigger | One of: `POST /minitos_core/api/bulk-create-container-visits/` with `{"containers":[{…, "category":"I", "ib_carrier":<vessel visit id>}]}`; the TCL-006 seed; or Django admin `/admin/minitos_core/containervisit/add/`. | `minitos_core/urls.py`, `views.py::bulk_create_container_visits`, `serializers.py::BulkContainerVisitSerializer.create` (sets `status=PREADVISED`) [code] |
| UNS/MQTT | **None today.** MiniTOS writes `IntegrationOutbox` rows only for site/configuration and order release (`site_sync.py`, `order_execution.py`), and `signals.py` has no receiver for `ContainerVisit`. Expected: a container-visit event on `scms/v2/{ent}/{site}/tos/…`, with schema id, topic and payload all **TBD** (TCL-003a's acceptance criteria list a "container-visit status" schema, but PR #13 doesn't contain one). Required fields per D-010: `container_visit_id` + ISO `container_number`, `category`, `ib_carrier`, `status`. | [code] grep of `IntegrationOutbox.objects.create`; Q2 |
| Sparkplug | none (no CHE involved) | — |
| MiniTOS state | `ContainerUnit(container_number=<ISO>, is_in_terminal=False)`. `ContainerVisit(category='I', status='Preadvised', ib_carrier=<vessel visit>, location=<terminal>, current_che=NULL, exception_reason='')`. No `Position` has `container_visit` = this visit. | `models.py::ContainerVisit`, `ContainerUnit`; `exception_reason` [PR #13] |
| OES state | none. OES holds no container state of its own (QA survey gap A2), and no `ExecutionOrder` exists. | `execution/models.py` |
| UI | MiniTOS admin changelist `ContainerVisit` (today shows "Preadvised"; label to show: "Inbound, advised on vessel"); DRF browsable API `/minitos_core/locations/{id}/container_visits/`. There is no dedicated visit page (only `landing`, `terminal-browser`, `terminal-editor`, `che_editor` and `berth_plan` templates). | `minitos_site/urls.py`, `minitos_core/templates/` |
| KG | TBD (Yael decides Fri) | D-004 |
| Historian | TBD (Yael decides Fri) | D-004 |

**Acceptance checks**
- **AC1.1** Exactly one `ContainerVisit` exists for the ISO number, with `category='I'`, `status='Preadvised'` and `ib_carrier` = the seeded vessel visit.
- **AC1.2** `current_che` is NULL, no `Position` references the visit, and `ContainerUnit.is_in_terminal` is False.
- **AC1.3** OES has no `ExecutionOrder` for the visit.
- **AC1.4** *(blocked, Q2)* One pre-advice event is seen on `scms/v2/#` within T s (Q10), it validates against its schema, and it carries both ids (D-010).
- **AC1.5** *(rerun, D-016)* After a reset and rerun, the visit's pk and state are the same (TCL-006 deterministic ids).

**Coverage:** no test covers the bulk-create endpoint or the `Preadvised` default. There's no event, so there's no contract test. Missing: AC1.1–AC1.5.

### Step 2: Vessel arrival. The vessel arrives with the container

**S1 scope:** not part of the S1 demo. The S1 script starts from the seeded reset and goes straight to release. In S1 this step is optional: show the seeded vessel visit state (a check, not an action). The broken transition API is bug **TCL-032**; the domain question is Q3a.

**Preconditions:** step 1 passed. The vessel visit is in `Created` or `Announced`.

| Aspect | Expected | Evidence |
|---|---|---|
| Trigger | Vessel visit status → `Arrived` (then `InOperation`, which in #4 should start dispatch; that's TCL-017, S4). API: `POST /minitos_core/api/visits/{visit_id}/transition-status/ {"status":"Arrived"}`. **This endpoint is broken on `main`:** `views.py` uses `VisitStateManager` and `VisitSerializer`, which are never defined or imported (pyflakes: `undefined name 'VisitStateManager'` at L1384/L1417, `'VisitSerializer'` at L1389). The call is outside the `try`, so it should return HTTP 500 [inferred, not run]. Workaround today: Django admin edit of `CarrierVisit.status`, which has no validation and no event. The admin panel is TCL-009 (S2). Bug: **TCL-032**. | `minitos_core/views.py::transition_visit_status`; TCL-032; Q3a |
| Simulator start (S1 precondition for step 3) | The Godot session is started from the OES console `/simulations/` with `make run-simulator-runner` running. | `README.md` |
| UNS/MQTT | **None today** for the visit status (same gap as step 1, Q2). Expected: a carrier-visit status event on `scms/v2/{ent}/{site}/tos/…`, schema TBD, with fields `carrier_visit_id`, `visit_type`, `status`, `ata`. | [code] |
| Sparkplug | When the session starts:<br>- **STATE:** `spBv1.0/STATE/{host}` = ONLINE (retained).<br>- **NBIRTH:** carries `Session/Id`, `Site/Id`, `Configuration/Revision`, `Configuration/Checksum` and `bdSeq`, plus [PR #15] `Contract/Version`, `Sim/Seed`, `Sim/StartEpochMs`, `Sim/Timing/QcHoldMinS`/`QcHoldMaxS`/`TtNoShowTimeoutS`/`CommandTtlS`.<br>- **DBIRTH:** one per CHE (`QC01`, `TT01`, `TT02`, `RTG01`), each with `Identity/*`, `State/*`, `Position/*` and `Carrying/ContainerVisitId=0`. | `UNS_CONTRACT.md`; NOTES-TCL-014a |
| MiniTOS state | `CarrierVisit.status='Arrived'`. Setting `ata` isn't implemented anywhere [code]. The container visit stays `Preadvised`: nothing in the code sets `OnVessel`, although the TCL-003a test fixture starts from `OnVessel` (`test_handoff_execution.py:48`). See Q4. | `models.py::CarrierVisit` |
| OES state | `SimulationSession` status `running`. `EquipmentState` exists per CHE: `device_connected`, `data_valid`, `is_available`, not `working`/`is_dispatched`, and `idle_since_event_ms` = session start [PR #13]. | `execution/models.py`; `test_handoffs.py::test_dbirth_sets_idle_since_from_session_start` |
| UI | MiniTOS admin `CarrierVisit` ("Arrived"); berth plan `/terminals/{id}/berth-plan/` shows generated visits (it doesn't display status, unverified). OES `/simulations/` shows the session; `/equipment/` lists the 4 CHEs. | templates |
| KG | TBD (Yael decides Fri) | D-004 |
| Historian | TBD (Yael decides Fri) | D-004 |

**Acceptance checks**
- **AC2.1** The vessel visit's status is `Arrived`, set through a supported path (API or panel), not a DB edit (MVP DoD 2).
- **AC2.2** The container visit's status matches Q4's answer (`Preadvised` or `OnVessel`).
- **AC2.3** OES shows one running session for the Site. NBIRTH revision/checksum = OES projection revision/checksum. All 4 CHEs are born, available and idle.
- **AC2.4** *(blocked, Q2)* A visit-status event is on the UNS and validates.
- **AC2.5** `POST …/transition-status/` returns 200 and a valid transition. An invalid one (e.g. `Departed` → `Arrived`) returns 400. Expected to fail until TCL-032 is fixed.

**Coverage:**
- Sparkplug NBIRTH/DBIRTH: covered in OES `execution/tests.py` and `test_handoffs.py` [PR #13], and in Godot `test_headless_sim.gd::_test_node_birth_metrics` [PR #15].
- Visit status transitions: no tests (`apps/minitos/TEST_COVERAGE_AUDIT.md` lists "visit status-transition endpoints" as uncovered and the state-manager symbols as a residual risk).
- Missing: AC2.1, AC2.2, AC2.4, AC2.5.

### Step 3: Discharge. The TT is sent to the QC, and the QC discharges the container onto the TT

D-005 and D-012 set the flow. The TT drives to the quay. The container *appears* on the QC; there is no lift from the vessel. The QC holds it for a seeded 10–30 s (whole seconds) and then deposits it on the TT, waiting for the TT if it's late. T-006: OES dispatches QC and TT together with a shared `HandoffId`.

**Preconditions:**
- Step 2 passed, and the session is running.
- A **discharge order** exists in MiniTOS:
  - `Order(order_type='IN', status='planned', container_number=<visit>)` with an active `OrderRoutePlan` and 3 legs: QC (seq 1, ends in a hand-off, so no `destination_inventory_position_id`), TT (seq 2, ends at the block lane, hand-off), RTG (seq 3, destination = a yard slot).
  - The 3-leg shape is set by T-006 and contract rev 9.3 (Q5, answered). Who creates it: the TCL-006 step command in S1, TCL-010 in S2.

**3a. Release (MiniTOS → OES)**

| Aspect | Expected | Evidence |
|---|---|---|
| Trigger | `POST http://localhost:${MINITOS_HTTP_PORT:-8000}/minitos_core/locations/{loc}/work_queues/{wq}/orders/{id}/release/` with an empty body, or the TCL-006 step command once it exists. There is no admin action for release.<br>- **Login:** `OrderViewSet` sets `permission_classes = [IsAuthenticated]`, which overrides the project default `DjangoModelPermissionsOrAnonReadOnly`. Any logged-in MiniTOS user may release, e.g. the superuser from `make create-minitos-admin`, through the DRF browsable API (after logging in at `/admin/`) or `curl -u <user> -X POST …` (DRF's default basic auth).<br>- **Ids:** `{loc}` = the terminal `Location` id, `{wq}` = the order's `WorkQueue` id, `{id}` = the MiniTOS `Order` id. All three are in the admin `Order` changelist, or `GET /minitos_core/locations/{loc}/work_queues/{wq}/orders/` (logged in).<br>- `release_order` only accepts `planned` and needs an active route plan with legs. | `minitos_core/views.py::OrderViewSet` (L543 on `main`), `OrderViewSet.release`; `minitos_site/settings.py` `REST_FRAMEWORK`; `order_execution.py::release_order` |
| UNS | Topic `scms/v2/{ent}/{site}/tos/orders/{order_id}/released`, schema `scms.order.released.v1`. Fields: `event_id`, `occurred_at`, `enterprise{id,code}`, `site{id,code,source_location_id}`, `terminal{id,code}`. `data.order{id, status:"released", priority, container_number (= the visit **pk**), container_unit_number (ISO, D-010) [PR #13], from_position, to_position, released_at}`. `data.legs[]{id, sequence, leg_kind, equipment_type, movement_mode, start/end_position, source/destination_inventory_position_id, destination_position_code [PR #13], source/destination lat/lon, planned_start/end}`. | `_queue_released_order`; `UNS_CONTRACT.md`; `test_handoff_execution.py::test_released_payload_carries_*` [PR #13] |
| MiniTOS | `Order.status='released'`, `released_at` set; `IntegrationOutbox` row published | — |
| OES | `InboxEvent` processed (idempotent on `event_id`). `ExecutionOrder(status='released', container_visit_id, container_number)`. 3 × `ExecutionOperation(status='planned')` with the frozen position ids. | `execution/services/ingestion.py::ingest_released_order` |

**3b. Paired dispatch (OES → Godot)**

| Aspect | Expected | Evidence |
|---|---|---|
| Trigger | OES API (S1 is manual; automatic dispatch is TCL-017, S4). Every `/api/` call needs the `X-OES-Token` header: the DRF default permission is `HasOESServiceToken`, and compose sets `OES_API_TOKEN` (from `.env`, else the compose default). Without it the call returns 403. Load the token from `.env` without writing it down: `set -a; . ./.env; set +a`.<br>1. Find the **OES** order id (the `id` field; the MiniTOS order id is `tos_order_id`, a different number):<br>`curl -s -H "X-OES-Token: $OES_API_TOKEN" "http://localhost:${OES_HTTP_PORT:-8010}/api/orders/?status=released"`<br>2. Dispatch:<br>`curl -s -X POST -H "X-OES-Token: $OES_API_TOKEN" -H 'Content-Type: application/json' -d '{}' "http://localhost:${OES_HTTP_PORT:-8010}/api/orders/<oes_id>/dispatch-next/"` → HTTP 201 (or use the TCL-006 step command if it wraps this).<br>A second call before H1 transfers sends **no new command**: HTTP 400 with the Q18 message on #13; HTTP 200 with `dispatched: false`, `code` and `detail` after #20 (TCL-028). | `execution/permissions.py::HasOESServiceToken`; `terminal_oes/settings.py` `REST_FRAMEWORK`; `compose.yaml` `OES_API_TOKEN`; `execution/views.py::ExecutionOrderViewSet` (`get_queryset` `status` filter, `dispatch_next`); `execution/models.py::ExecutionOrder.tos_order_id`; `dispatch.py::dispatch_next_operation` [PR #13]; TCL-028 `views.py::dispatch_next` [PR #20]; `test_qc_and_tt_are_dispatched_together_with_one_handoff_id` |
| TT choice | D-014: the longest idle on `Event/TimeMs`, ties broken by a PRNG seeded with `Sim/Seed:OrderId`. With no seed and a tie, it's an error. | `dispatch.py::select_equipment` [PR #13]; `test_d014_idle_longest_on_event_time`, `test_tie_break_is_seeded_from_sim_seed_and_order_id` |
| Sparkplug | **DCMD** `spBv1.0/{group}/DCMD/{node}/QC01`:<br>- `Command/Type=qc_discharge`, `HandoffRole=giver`<br>- `HandoffId=H1`, `CounterpartCheId=<TT>`, `TransferPositionId`<br>- `ContainerVisitId`, `ContainerNumber`<br>- `ExpiresAtMs` (sim time, + `CommandTtlS`, default 600)<br>- plus the base envelope (`Id`, `AssignmentId`, `SessionId`, `SiteId`, `ConfigurationRevision`, `CheId`, `OrderId`, `LegId`)<br><br>**DCMD** `…/TTxx`:<br>- `Command/Type=execute_order_leg`, `HandoffRole=receiver`<br>- the same `HandoffId`, `CounterpartCheId=<QC>` | `UNS_CONTRACT.md` § Hand-offs [PR #13] |
| UNS | `…/oes/orders/{id}/execution` (`scms.execution.event.v1`): `leg_dispatched` (leg 1, `che_name` QC01), then `leg_dispatched` (leg 2, TT) | [run] |
| OES | `ExecutionOrder.status='in_progress'`, `started_at` set. Ops 1–2 are `dispatched`, with 2 `DispatchAssignment` rows. 2 `EquipmentCommand` rows go `pending` → `published`. `Handoff(H1, status='pending')`. Op 3 is still `planned`. | `execution/models.py` [PR #13] |
| MiniTOS | Legs 1–2 `dispatched` with `che` set. QC and TT: `is_dispatched=True`, `is_available=False`. | `order_execution.py::apply_execution_event` (`leg_dispatched`) |

**3c. The container appears on the QC (D-012)**

| Aspect | Expected | Evidence |
|---|---|---|
| Sparkplug | QC01 DDATA results at the **same** `Sim/TimeMs`: `acknowledged`, `started`, then `container_picked_up` with `Command/Result/Source=VESSEL`. Every result carries `Command/Result/Id` (uuid5 of `Session/Id:Sim/EventSeq`), `Session/Id`, `Sim/TimeMs`, `Event/TimeMs` and `Sim/EventSeq`. | NOTES-TCL-014a [PR #15]; `Command/Result/Source` is an assumed name (NOTES open point 1) |
| UNS | `leg_started` (leg 1, QC01), then `container_picked_up`. The second carries `source:"VESSEL"`, `container_visit_id`, `container_number` (ISO), `che_id`, `che_name:"QC01"`. | [run]; `dispatch.py::apply_command_event`; golden `contracts/golden/scms.execution.event.v1/03_container_picked_up.json` [PR #14] |
| MiniTOS | `place_container_on_quay_crane`: `ContainerVisit.status='OnEquipment'`, `current_che=QC01` (D-015), lat/lon = QC. It rejects a non-QC CHE, a visit already in a yard slot, or a closed visit. Leg 1 is `working`, the order is `in_progress`. | `order_execution.py` [PR #13]; `test_vessel_pick_places_the_container_on_the_qc`, `test_vessel_pick_by_a_non_qc_is_rejected` |
| OES | QC command `acknowledged`, op 1 `working`. `CommandResultReceipt` rows are `applied`; `container_picked_up` is also `informational` for the pair. | `test_qc_tt_rtg_import_completes_with_one_custody_event_per_handoff` |

**3d. TT arrives, QC holds, then QC→TT transfer**

| Aspect | Expected | Evidence |
|---|---|---|
| Sparkplug | TT results: `acknowledged`, `started`, `arrived_at_source` and `waiting_for_handoff` (both informational). The QC holds for h ∈ [10, 30] whole sim seconds, seeded (**compressed time**). The deposit happens at `max(hold end, TT ready)`. Both sides then publish `handoff_completed` (QC giver, TT receiver), with `FromCheId`, `ToCheId`, `TransferPositionId` and `PositionId` = lane. Then QC `completed`. No `container_dropped` or `container_picked_up` is sent for the transfer. | NOTES-TCL-014a [PR #15] |
| UNS | `leg_started` (leg 2, TT), then `container_transferred`. That event carries `handoff_id`, `from_che_id`/`from_che_name` QC01, `to_che_id`/`to_che_name` TTxx, `giver_leg_id`, `receiver_leg_id`, `transfer_position_id`, `container_visit_id`, `container_number`; its `event_id` is uuid5 of the `handoff_id`. Then `leg_completed` (leg 1, QC01). | [run]; golden `handoff/01_container_transferred_qc_to_tt.json` [PR #14] |
| MiniTOS | `transfer_container`: `current_che=TTxx`, status stays `OnEquipment`, lat/lon = TT. Leg 1 `complete`; QC is available again. | `order_execution.py::transfer_container` [PR #13]; `test_transfers_move_custody_che_to_che_once` |
| OES | `Handoff(H1).status='transferred'`, `transferred_event_ms` set. Op 1 `complete`; op 2 `working`. The QC is available again, with `idle_since_event_ms` updated. | `handoffs.py::apply_handoff_completed` [PR #13] |
| UI | MiniTOS admin `ContainerVisit`: "On Equipment" with current CHE = QC01, then TTxx. OES `/equipment/`: QC/TT Working/Dispatched flags and raw telemetry. OES API `/api/orders/{id}/`, `/api/commands/`. Godot is headless, so there's no 3D view in S1. | templates; S1 note risk 7 |
| KG | TBD (Yael decides Fri) | D-004 |
| Historian | TBD (Yael decides Fri) | D-004 |

**3x. Exception path: TT no-show (D-013, D-017)**

If the TT hasn't arrived N s after the hold ends (N from NBIRTH `Sim/Timing/TtNoShowTimeoutS`, else `SIMULATOR_TT_NO_SHOW_TIMEOUT_S`, else 300; configurable per D-017), the QC publishes `handoff_timed_out` once. OES then:
- records `Handoff.status='exception'`;
- emits `handoff_exception` with `reason:"SIMULATOR_FAILURE"`, `detail:"no TT within <N> s"` (e.g. "no TT within 300 s" with the default), the hand-off ids and the names;
- sends cancel DCMDs (`Command/Type=cancel`, `CancelCommandId`, `Reason=SIMULATOR_FAILURE`) for both legs.

This is a **simulator failure marker, not an operational recovery** (SME review: a real terminal would send another TT). Present it as "simulator failure: container placed administratively"; whether it goes to a separate exception area and is excluded from KPIs is Q16.

MiniTOS then moves the visit to a free `BLOCK01` slot, sets `AtYard` and `exception_reason`/`exception_detail`, completes the order **by exception** (`complete`), cancels the open legs and frees both CHEs.

Evidence: `handoffs.py::no_show_detail`, `apply_handoff_timed_out` and `order_execution.py::relocate_container_after_exception` [PR #13]; `test_tt_no_show_raises_one_exception_and_cancels_both_legs`, `test_no_show_exception_relocates_to_block01_flagged`; golden `handoff/03_handoff_exception_simulator_failure.json` [PR #14]; Godot `_test_sim_tt_no_show_then_cancel` [PR #15].

**Acceptance checks**
- **AC3.1** The released payload validates (schema TBD for `order.released`; see gap G4). `container_number` = the visit pk and `container_unit_number` = the ISO number.
- **AC3.2** One `dispatch-next` call produces exactly 2 DCMDs: QC `qc_discharge` (giver) and TT `execute_order_leg` (receiver), with the same `HandoffId`, crossed `CounterpartCheId`, and a sim-time `ExpiresAtMs`. The RTG op stays `planned`. A second call before H1 transfers sends **no new DCMD**, and the response says why (400 with the Q18 message on #13; 200 `dispatched:false` with `code`/`detail` after #20).
- **AC3.3** The TT chosen is the one idle longest. On a tie, the same seed gives the same TT across reruns (D-014, D-016).
- **AC3.4** Ordered UNS sequence for the order:
  1. `leg_dispatched`(1)
  2. `leg_dispatched`(2)
  3. `leg_started`(1)
  4. `container_picked_up`(1, source VESSEL)
  5. `leg_started`(2)
  6. `container_transferred`(QC→TT)
  7. `leg_completed`(1)

  Every event validates against `scms.execution.event.v1` and carries `terminal.id` (QA survey gap B3: the terminal must be in every envelope).
- **AC3.5** After (4), MiniTOS shows `OnEquipment` with `current_che` = QC01 and no slot. After (6), it shows `OnEquipment` with `current_che` = TT, with exactly one location at all times.
- **AC3.6** Sim time from appear to deposit (compressed time) is a whole number in [10, 30] if the TT was waiting, else the deposit happens at TT ready (D-012). Same seed, same hold sequence (D-016).
- **AC3.7** No `CommandResultReceipt` is `rejected`, and no MiniTOS `IntegrationInbox` row is `failed`.
- **AC3.8** No-show (fields checked on #13):
  - **AC3.8a** OES `Handoff`: `status='exception'`, `exception_reason='SIMULATOR_FAILURE'`, `exception_detail='no TT within <N> s'`.
  - **AC3.8b** OES `ExecutionOrder`: `status='complete'`, `failure_reason='SIMULATOR_FAILURE'`. Open `ExecutionOperation`s and `DispatchAssignment`s are `cancelled` with `failure_reason='SIMULATOR_FAILURE'`, and cancel DCMDs are sent.
  - **AC3.8c** MiniTOS `ContainerVisit`: `status='AtYard'` in exactly one `BLOCK01` slot, `current_che` NULL, `exception_reason='SIMULATOR_FAILURE'`, `exception_detail` = the event's `detail`.
  - **AC3.8d** MiniTOS `Order`: `status='complete'` with the open legs `cancelled`. The MiniTOS `Order` has **no** reason field; the reason lives on the visit.
  - **AC3.8e** The QC and TT are available and not dispatched in both systems.
- **AC3.9** The duplicate DCMD and duplicate result (QoS 1) are each applied once (`Command/Result/Id`, `event_id`).

**Coverage:**
- **Per service, on open PRs:**
  - OES `test_handoffs.py` (AC3.2–3.4, 3.7–3.9) [PR #13]
  - MiniTOS `test_handoff_execution.py` (AC3.5, 3.8) [PR #13]
  - contract tests `test_contract_execution_events.py` / `test_contract_handoff_events.py` in both services (AC3.4 shape) [PR #14]
  - Godot `test_headless_sim.gd` (`_test_qc_state_machine_*`, `_test_sim_qc_tt_*_end_to_end`, `_test_sim_replay_same_seed`; AC3.6 per service) [PR #15]
  - D-012/D-013 acceptance in `tests/requires-TCL-014a/test_acceptance_d012_d013.gd` [PR #14], which isn't wired into `make test-godot`.
- **Missing:**
  - (a) A cross-service run (compose: MiniTOS + OES + HiveMQ + Godot) asserting AC3.4/3.5 on the real broker. That's TCL-007, S2.
  - (b) The released payload of a real 3-leg discharge order built by MiniTOS. The tests build the legs by hand.
  - (c) An `order.released.v1` JSON Schema and goldens (G4).
  - (d) Shared Python/GDScript golden vectors for the `qc_discharge` DCMD and the `handoff_completed` result.
  - (e) The hold distribution across seeds (QA survey cases D012-3/4: holds stay in [10, 30] and vary with the seed).

### Step 4: TT takes the container to the yard. The TT drives to the yard location, and the RTG takes the container and places it in the yard

**S1 timeout caveat:** in S1 there is **no timeout** while the TT waits at the block for the RTG (contract §3.3a; O7/O8 pending Yael; `SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED` / `Sim/Timing/RtgNoShowTimeoutEnabled` default `false` [PR #17]). If H2 never completes, the run stalls at `waiting_for_handoff` with no exception. Stop the session and reset (`make scenario-reset SCENARIO=import_v1`).

**Preconditions:** step 3 passed: `Handoff(H1)` is `transferred`, and the TT carries the container. RTG01 is idle in `BLOCK01`. The RTG leg's destination slot is free.

| Aspect | Expected | Evidence |
|---|---|---|
| Trigger | The same `curl … /api/orders/<oes_id>/dispatch-next/` with `X-OES-Token` as in 3b, after H1 has transferred (Q18; manual until TCL-017). | `dispatch.py::_plan_dispatch` (chained) [PR #13] |
| Sparkplug | **DCMD** `…/TTxx`:<br>- `Command/Type=assign_handoff`, `HandoffRole=giver`<br>- `HandoffId=H2`, `CounterpartCheId=RTG01`, `TransferPositionId`<br>- `LegId` = the TT leg, `ContainerVisitId`<br><br>**DCMD** `…/RTG01`:<br>- `Command/Type=execute_order_leg`, `HandoffRole=receiver`<br>- `DestinationPositionId`, `DestinationPositionCode`, `DestinationInventoryPositionId` (the MiniTOS slot), `DestinationLatitude`/`Longitude`<br><br>**Results:**<br>- TT: `arrived_at_destination`, `waiting_for_handoff`<br>- RTG: `acknowledged`, `started`, `handoff_completed` (receiver, the pick)<br>- TT: `handoff_completed` (giver, sent on the assign id), then `completed` (TT leg)<br>- RTG: seeded hold 10..30 s (compressed time), `container_dropped` echoing one `Command/Result/PositionId` (the inventory id) plus `Command/Result/PositionCode` (O11, rev 9.3), then `completed`<br>The O12 pre-send consistency guard (OES validates the destination ids before sending) isn't built yet (backlog, S2). | `UNS_CONTRACT.md` [PR #13]; NOTES-TCL-014b [PR #17] |
| UNS | `leg_dispatched`(3, RTG01), `leg_started`(3), `container_transferred` (TT→RTG01), `leg_completed`(2, TT), `container_dropped`(3, RTG01, `position_id`=slot), `leg_completed`(3) | [run]; golden `handoff/02_container_transferred_tt_to_rtg.json`, `04_container_dropped.json` [PR #14] |
| MiniTOS | After the transfer: `current_che=RTG01`. After the drop: `Position(slot).container_visit=<visit>`, `ContainerVisit.status='AtYard'`, `current_che=NULL`, lat/lon = the slot's operational position, `ContainerUnit.is_in_terminal=True`. All legs `complete`, `Order.status='complete'`, `completed_at`/`atc` set. TT and RTG available again. | `order_execution.py::drop_container`, `apply_execution_event` (`leg_completed`) |
| OES | `Handoff(H2)='transferred'`. Ops 1–3 `complete`; `ExecutionOrder='complete'`. Every command `complete`, no `rejected` receipts. QC, TT and RTG available, not dispatched. | `test_qc_tt_rtg_import_completes_with_one_custody_event_per_handoff` |
| UI | MiniTOS admin `ContainerVisit` "At Yard"; admin `Position` shows the visit in the slot. Terminal browser `/terminal-browser/` (whether it shows occupancy is unverified). OES `/equipment/`: all CHEs idle. | templates |
| KG | TBD (Yael decides Fri) | D-004 |
| Historian | TBD (Yael decides Fri) | D-004 |

**Acceptance checks**
- **AC4.1** The second `dispatch-next` produces exactly 2 DCMDs: TT `assign_handoff` (giver, H2) and RTG `execute_order_leg` (receiver), with the destination inventory id = the order's planned slot.
- **AC4.2** Ordered UNS continues after AC3.4:
  1. `leg_dispatched`(3)
  2. `leg_started`(3)
  3. `container_transferred`(TT→RTG)
  4. `leg_completed`(2)
  5. `container_dropped`(3, slot)
  6. `leg_completed`(3)

  Exactly 2 `container_transferred` and 1 `container_picked_up` in the whole order.
- **AC4.3** MiniTOS: the visit is in exactly one slot (the planned one), `AtYard`, with no CHE. No other slot references it, and the slot isn't double-booked. `is_in_terminal=True`.
- **AC4.4** OES and MiniTOS both report the order `complete`, and all 4 CHEs are available.
- **AC4.5** The Godot RTG drops into the slot OES sent, not a seeded dummy slot. That fallback is only for when no id is sent (O6). Until the O12 guard exists, this relies on MiniTOS sending consistent destination ids.
- **AC4.6** Rerun from reset: the same states in the same order for steps 1–4 (D-016), with the same TT choice and the same hold sequence.
- **AC4.7** RTG/TT no-show at the block: TBD (O7/O8, Yael). In S1, a stall means stop and reset (see the caveat above).

**Coverage:**
- **Per service, on open PRs:**
  - OES happy path (AC4.1, 4.2, 4.4) [PR #13]
  - contract goldens TT→RTG and drop [PR #14]
  - Godot `RtgStack` tests on TCL-014b [PR #17]
  - MiniTOS `drop_container`, and D-015 pickup/drop in `test_acceptance_rules.py` [PR #14]
- **Missing:**
  - (a) A MiniTOS test that feeds the **whole** steps-3–4 OES sequence through the consumer and asserts AC4.3. Today each event is tested in isolation.
  - (b) A cross-service compose run (TCL-007/TCL-022a).
  - (c) AC4.5 against OES DCMDs over a real broker.
  - (d) AC4.6 full-stack (QA survey case D016-2: full rerun gives the same states; blocked on TCL-006).

## 4. Steps 5–9 (outline; TBD unless noted)

| # | Step | Trigger | UNS / Sparkplug | MiniTOS | OES | Known code today | KG | Historian |
|---|---|---|---|---|---|---|---|---|
| 5 | Yard dwell | Time passes. Is it a sim-time or a wall-clock wait? (Q8) | TBD. Is any event expected during dwell? (Q8) | `AtYard`, slot unchanged | none | Nothing to build except the dwell duration | TBD (Yael decides Fri) | TBD (Yael decides Fri) |
| 6 | Truck booked | A scenario driver (T-005, TCL-024) or a MiniTOS user creates the truck visit and booking | TBD (TCL-003b) | `CarrierVisit(visit_type='Truck', status='Created')`, `ContainerVisit.ob_carrier` = truck visit; a delivery order (`order_type='OT'`) TBD | none | The `CarrierVisit` Truck type exists; there's no truck-visit/gate model (#9) | TBD (Yael decides Fri) | TBD (Yael decides Fri) |
| 7 | Truck arrival | Scripted gate-in (T-005) | TBD (TCL-003b). The truck isn't a Sparkplug device under T-005. | truck visit `Arrived` TBD | Delivery order released → `ExecutionOrder` TBD | none | TBD (Yael decides Fri) | TBD (Yael decides Fri) |
| 8 | Departure: RTG loads the truck | OES dispatch of the yard→truck RTG leg | DCMD RTG `execute_order_leg`; `container_picked_up` (from the yard slot), then the drop onto the truck: TBD. There's no truck position type, and `drop_container` needs a yard `Position`. | `pickup_container` clears the slot → `OnEquipment`/RTG01; after that TBD | TBD | RTG yard→truck is out of scope in TCL-014b | TBD (Yael decides Fri) | TBD (Yael decides Fri) |
| 9 | Visit closure | Scripted gate-out (T-005) | TBD (TCL-003b) | `ContainerVisit.status` `Departed` then `Completed`? `ContainerUnit.is_in_terminal=False`; truck visit `Departed` (Q9) | TBD | Nothing sets `Departed`/`Completed` from an event | TBD (Yael decides Fri) | TBD (Yael decides Fri) |

These issue-level requirements apply after step 9:
- **R1:** the final visit history can be inspected (DoD 4, TCL-021).
- **R2:** the scenario reruns from the known state (DoD 1, 5; TCL-006). It isn't built yet.

## 5. Test coverage map

| Check | Exists (branch) | Missing / owner |
|---|---|---|
| AC1.* pre-advice | — | MiniTOS API test for bulk create + an event contract test (after Q2) |
| AC2.1/2.5 visit transition | — | TCL-032 (the `VisitStateManager` NameError); API tests; panel (TCL-009) |
| AC2.3 session birth | OES `tests.py`, `test_handoffs.py` [#13]; Godot `_test_node_birth_metrics` [#15] | Cross-service (TCL-007) |
| AC3.1 released payload | `test_released_payload_carries_*` [#13] | `order.released.v1` schema + goldens; a real 3-leg discharge order (TCL-006/TCL-010) |
| AC3.2–3.4, 3.7–3.9 | OES `test_handoffs.py` [#13]; contract tests [#14] | Broker-level run |
| AC3.5 custody | MiniTOS `test_handoff_execution.py` [#13]; `test_acceptance_rules.py` [#14] | Whole-sequence consumer test |
| AC3.6 hold | Godot `test_headless_sim.gd` [#15]; `requires-TCL-014a/test_acceptance_d012_d013.gd` [#14, not wired] | Wire it into `make test-godot` / TCL-029 CI |
| AC3.8a–e no-show | OES, MiniTOS [#13]; Godot [#15]; golden [#14] | Cross-service |
| AC4.1–4.4 | OES happy path [#13]; goldens [#14]; Godot TCL-014b [#17] | MiniTOS whole-sequence; cross-service |
| AC4.5 RTG slot | Godot TCL-014b [#17] | Broker-level; O12 guard (S2) |
| AC4.7 block no-show | — | TBD (O7/O8) |
| AC1.5, AC4.6 rerun | OES `test_handoffs.py::test_d016_same_seed_same_states_in_the_same_order` [#13]; `execution/test_acceptance_rules.py::test_d016_rerun_produces_same_state_sequence_not_same_messages` [#14] | Full stack (needs TCL-006) |
| Steps 5–9 | — | TCL-003b, 018, 024, 022b |
| KG / historian per step | — | After Yael's decision (TCL-019/020) |

**Gaps found while writing this spec** (not fixed here; for the Tech Lead to assign):
- **G1:** MiniTOS emits no UNS event for container-visit or carrier-visit status (steps 1, 2, 9).
- **G2:** `transition_visit_status` / `transition_container_status` reference the undefined `VisitStateManager` / `VisitSerializer` (pyflakes on `main` @ `9c4b96d`). A request is expected to return 500 [inferred]. Tracked as **TCL-032**.
- **G3:** Nothing sets `ContainerVisit.status='OnVessel'` or `CarrierVisit.ata`/`atd`.
- **G4:** There's no JSON Schema or golden for `scms.order.released.v1` (TCL-005 covers only `scms.execution.event.v1`).
- **G5:** `ContainerUnit.is_in_terminal` turns True only at the yard drop, not when the container appears on the QC.
- **G6:** No MiniTOS path creates the 3-leg discharge order (QC→TT→RTG). The tests build it by hand.

**Follow-ups from the SME review** (not spec changes):
- **F1 (S2):** rename the released-payload field `container_number` (it holds the visit pk) to `container_visit_id`, and use `container_number` for the ISO number. The SME has logged it for S2.
- **F2:** the test fixtures and goldens on #13/#14 use `MSCU1234565`, whose ISO 6346 check digit is wrong; the valid number is `MSCU1234566`. `ContainerUnit` doesn't validate the check digit today.

## 6. Open questions for Yael

These go through the Tech Lead. QA doesn't decide them. Each has the SME's recommendation (SME review of TCL-001, 27 Oct, team notes); Yael decides. Numbers are kept stable so earlier references still work.

| Q | Status | Question | SME recommendation |
|---|---|---|---|
| **Q1** Seed vs live step 1 | **Answered for S1** by the sprint plan (TCL-006 seeds the advised container). Open only for the 1 Dec MVP demo. | For the MVP demo, is step 1 a seeded check or a live "discharge list received" action? | Seed it, and present it as "discharge list received" with a visible timestamp. A live load action can come later. |
| **Q2** UNS events for steps 1–2 (and 9) | **TL/BE** (architecture), then Yael for the sprint. Issue #1 already requires every step to pass through the UNS. | (a) In which sprint do the visit-status events land (TCL-003b)? (b) What granularity: per-entity status-changed or per business step (TL/BE call)? (c) For Yael: may the S1 demo show steps 1–2 without an event? | Per-entity status-changed events (`container_visit`, `carrier_visit`) carrying industry milestone names; see Q14. |
| **Q3a** What counts as "vessel arrival" (domain) | Open. SME first, then Yael. | Is "vessel arrival" `Arrived` (ATA) or `InOperation` (berthed, operations started)? | Show both: `Arrived` (ATA), then `InOperation` (ATB/operations start). See Q13. |
| **Q3b** Transition API broken | **Not a question:** bug **TCL-032**. | — | Fix before AC2.1 can pass through a supported path. |
| **Q4** Container status on the vessel | Open (SME reviewed). | When the vessel arrives, should import containers move `Preadvised` → `OnVessel`? | No transition at arrival. Use one pre-discharge status meaning "inbound, advised on vessel" from step 1 (rename the label or create visits as `OnVessel`), and align the TCL-003a fixture with it. |
| **Q5** Discharge order shape | **Answered** by T-006 and contract rev 9.3 (TL statement Yael can object to): one order with 3 legs QC→TT→RTG. Creator: TCL-006 step command in S1, TCL-010 in S2. | — | Agrees: one order with 3 legs; in S2 generate it from a "QC01 discharge work queue". |
| **Q6** When is it "in terminal" | Open (SME reviewed). | Is the container "in the terminal" when it appears on the QC, at the QC→TT transfer, or only in the yard (today: yard drop, G5)? | At the QC→TT transfer (discharge confirmation). Keep `OnEquipment`/TT for location. See Q15. |
| **Q7** D-013 "ready" | **Merged into D-013**: the same open item as the Tech Lead's reading of D-013 (count from the end of the D-012 hold). Not asked separately. | — | Confirm: count from the end of the hold; treat the timeout as a simulator-failure detector only. |
| **Q8** Yard dwell | Open. | How long is the dwell (sim time) in the demo, and must anything be visible during it? | Jump sim time by about 3 days; show "dwell so far" from the discharge time; emit the customs/line release events during dwell (Q17). |
| **Q9** Visit closure | Open. | Which final states: `ContainerVisit` `Departed` and/or `Completed`, and truck visit `Departed`? Does closure need its own event? | `Departed` at gate-out with its own status-changed event; truck visit `Departed`; `Completed` only as a later administrative close. |
| **Q10** Timing tolerance | Open. **Proposal (QA/TL), for Yael to decide:** demo ≤ 5 s from a simulator result to a consistent MiniTOS and OES state; CI ≤ 10 s per step, with event order asserted strictly. | Do you accept the proposed bounds? | Tighter: demo ≤ 2 s, CI ≤ 5 s; assert ordering strictly and latency loosely. |
| **Q11** UI for S1 | **Merged into the Fri 30 Oct question** "should the demo show Godot visually, or are the UNS and consoles enough?". | — | Admin and OES console are acceptable for the S1 internal review only; fix the display labels to industry terms now; the ex-colleague demo needs the visit timeline (TCL-021). |
| **Q12** KG and historian | Open; Fri 30 Oct decision (D-004). | What must each step show in the KG and the historian? Every KG/Historian cell above is a placeholder. | KG: current-state edges plus immutable event nodes per milestone. Historian: CHE states, waiting times and milestone events keyed on sim time. |
| **Q13** ATA/ATB split (SME) | New. | Should the vessel visit record ATA and ATB/operations start as separate milestones, with discharge release/dispatch allowed only in `InOperation` (and an AC for it)? | Yes, before the 1 Dec demo. |
| **Q14** Business milestone events (SME) | New. Related to Q2. | Should the UNS carry business milestones (Advised, Arrived, Working, Discharged, InYard, Released, Departed) in addition to the CHE execution events? | Yes; it covers steps 1, 2 and 9 and the "discharged"/"in yard" views in one schema. |
| **Q15** Custody and dwell start at the QC→TT transfer (SME) | New. Related to Q6. | Should terminal custody (`is_in_terminal`) and the dwell clock start at the QC→TT transfer rather than at the yard drop? | Yes: that is when the box is discharged. |
| **Q16** No-show box to an exception area outside KPIs (SME) | New. D-017 stands; this is about presentation. | Should the D-017 no-show container go to a dedicated exception area (e.g. `EXC01`) instead of `BLOCK01`, be labelled "simulator failure: placed administratively", and be excluded from KPIs (dwell, cycle times)? | Yes to all three. |
| **Q17** Customs and line holds (SME) | New. | Should the demo container carry customs and line holds from step 1, released during dwell and checked at truck booking and gate-in? | Yes: the holds are what make the import story real. |

## 7. What this spec did not verify

- No Docker on QA's box: nothing here was run through compose, HiveMQ or a live Godot process.
- The [run] event order comes from TCL-003a's own OES test scenario (`run_happy_path`), executed at the TCL-005 tip `9785428` on SQLite.
- The Godot behaviour comes from reading NOTES-TCL-014a and NOTES-TCL-014b and the test names. QA last ran the Godot tests at an earlier TCL-014a commit, not at `4dec5a1`.
- The UI descriptions come from the templates and URL confs; no browser was used.
- G2 (HTTP 500) is inferred from static analysis.
- v2 (review fixes): the `curl` commands, the `X-OES-Token` header, the `?status=released` filter, the 400/200 second-call responses, the release permission and the AC3.8 field names were checked by reading the code on the branches above. None of the commands was executed against a running stack.
