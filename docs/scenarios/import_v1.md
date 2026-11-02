# Import scenario `import_v1`: acceptance spec (TCL-001)

- **Scenario:** import container visit, issue [#1](https://github.com/yohhalley/terminal-scms/issues/1). The export visit (#2) is out of scope here (D-002: import first).
- **Owner:** QA (Moon Unit). **Reviewers:** SME (TCL-015) and Tech Lead.
- **Status:** draft v2.1, 27 Oct 2026. v2 addresses the Tech Lead's and SME's reviews of PR #19. v2.1 (TCL-001a) rewrites step 2 for the fixed visit-transition API (TCL-032, #21) and updates the merge notes. v2.2 (31 Oct 2026) applies Yael's decision D-018: the seeded vessel visit starts as `Arrived` (K4, step 2). v2.3 (2 Nov 2026, TCL-001c) applies Yael's group-A and group-B answers (D-024 to D-027, D-028 to D-043); each change is marked with its D-number, and §6 maps every decision to the parts it changes. Steps 1–4 are complete. Steps 5–9 are an outline. The KG and historian engines are decided (Neo4j with Cypher, D-045; TimescaleDB, D-044), but what each step shows there is still TBD (TCL-019, TCL-020; Q12).
- **Code base read:** `main` @ `83949ef` (27 Oct 2026). These PRs are merged into `main`:
  - #13 TCL-003a (OES hand-off dispatch)
  - #14 TCL-005 (contract tests)
  - #15 TCL-014a (Godot QC→TT)
  - #16 TCL-029 (Godot CI)
  - #18 TCL-004 (LAN IP placeholder)
  - #20 TCL-028 (dispatch-next API)
  - #21 TCL-032 (visit-transition fix)

  Still open: #17 TCL-014b (Godot TT→RTG).

  v2.3 is a docs-only update on `main` @ `19b7c3d` (PRs #24 TCL-006 and #25 TCL-001b merged). QA did not re-read or re-run the code for v2.3: the **[code]**/**[run]** markers still refer to `83949ef`. Behaviour that a decision requires but the code doesn't have yet is marked **(not built)** with the task that builds it. A marker **[PR #n]** / **[#n]** names the PR that introduced a claim. For all PRs above it is now on `main`; a **[PR #17]** claim is true only on that branch.
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
  - **Compressed time / timing profile (D-039):** today the QC and RTG holds are 10–30 s (D-012), far shorter than real cycles. D-039 makes the crane timing a simulation-initialisation parameter: **fast** timings for tests and CI, **realistic** timings (QC ~90–120 s per cycle, RTG ~60–120 s) for the live demo only. Don't read productivity rates from a fast-profile run. The fast profile's exact values and how a realistic cycle splits into hold/move phases are not specified (Q22).

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
| Timing profile (D-039) | sim-init parameter: `fast` in tests and CI, `realistic` (QC ~90–120 s, RTG ~60–120 s) in the live demo only. **(not built:** S2 "Sim params" task) | D-039 |
| Live demo speed (D-040) | the live demo runs at real time (×1): sim time = wall-clock time. CI speed is not stated (Q24). | D-040 |
| Timing limits (D-037) | MiniTOS and OES show a consistent state within **2 s in the demo** and **5 s per step in CI**; event order is checked strictly. Replaces the Q10 proposal (5 s / 10 s). | D-037 |
| Simulation failures (D-027) | every CHE (QC, TT, RTG) uses the same failure logic as D-017: on time-out, record `SIMULATOR_FAILURE`, let the flow continue, mark it as a simulation issue. The time-out is a user-adjustable sim-init parameter (default 300 s). **(not built** for the RTG ↔ TT wait: S2 "Sim params" task) | D-027, D-017 |
| Container display (D-041) | demo screens show the container number (valid ISO 6346 check digit) and its slot on the vessel | D-041 |
| Equipment view (D-028, D-029) | Godot stays headless, with no 3D view. Equipment real-state visualisation lives in OES (equipment-state view, MVP, scheduled after S2). MVP telemetry: position and a "twistlocks locked" signal only. | D-028, D-029 |

## 1. Seeded known state (precondition for step 1)

There is no seed or reset yet. It is TCL-006 (FE), which is not on origin. The S1 sprint note sets the target: 1 quay, 1 block, roads, 1 QC, 2 TT, 1 RTG, and 1 vessel visit with 1 pre-advised container (room for 5). It also requires deterministic UUIDs, a seeded RNG, and `make scenario-reset SCENARIO=import_v1`, which resets both DBs, clears retained MQTT and publishes the Site. Until TCL-006 lands, every check below assumes this state:

| ID | State | Where it lives |
|---|---|---|
| K1 | Enterprise + Site published: OES `SiteProjection.projection_status = ready`, revision N, checksum matches | MiniTOS `site_sync.py` → `tos/site/created`, `tos/configuration/published`; OES `execution/services/site_sync.py` |
| K2 | Layout with quay/berth, block `BLOCK01` with free ground slots, transfer lane(s), roads | MiniTOS `Position`, `OperationalPosition`, `StorageGrid` (`BLOCK01` is the exception block in `order_execution.py::EXCEPTION_BLOCK_NAME`) [PR #13] |
| K3 | CHEs `QC01` (type STS/QC), `TT01`, `TT02`, `RTG01`, all available and not dispatched | MiniTOS `Che`; OES `EquipmentState` after DBIRTH |
| K4 | One vessel `CarrierVisit` (`visit_type='Vessel'`, status `Arrived`, `ata` = the seed epoch). Yael's decision D-018 (31 Oct 2026): the seed starts the visit as `Arrived`, not `Created`; the `Created` → `Arrived` transition is deferred to after the MVP (backlog). `Arrived` means berthed and moored, not necessarily operational (D-024). | `models.py::CarrierVisit`; `scenarios/import_v1.py` |
| K5 | One `ContainerUnit` with a valid ISO 6346 number, e.g. `MSCU1234566` (check digit 6, computed by QA), `is_in_terminal=False`, and its import `ContainerVisit` **seeded** as inbound, advised on the vessel (S1 sprint plan and TCL-006 acceptance; D-031). Its status is the single pre-discharge status "advised, on inbound vessel" (D-032). MVP, **not built** (S3–S4): separate customs, shipping-line and terminal holds on this container from step 1 (D-035). Note: the existing test fixtures and goldens use `MSCU1234565`, whose check digit is wrong (follow-up F2). | `models.py::ContainerUnit` |
| K6 | No OES orders; one planned MiniTOS order: the 3-leg discharge order for `MSCU1234566` (`Order(order_type='IN', status='planned')`, legs QC → TT → RTG; see step 3 preconditions). No commands or hand-offs in either DB. MQTT retained topics are cleared except `spBv1.0/STATE/{host}`. | — |
| K7 | Seed values: `SIMULATOR_SEED=1`, `SIMULATOR_START_EPOCH_MS=1792886400000`, QC hold 10..30 s (compressed time; becomes the `fast` timing profile, D-039), TT no-show timeout configurable per D-017: NBIRTH `Sim/Timing/TtNoShowTimeoutS`, else `SIMULATOR_TT_NO_SHOW_TIMEOUT_S`, else 300 s. D-027: one user-adjustable failure time-out per CHE type, set at sim init (not built for RTG; S2 "Sim params"). | `compose.yaml` [PR #13], NOTES-TCL-014a |
| K8 | *(MVP, not built; not in S2)* Some containers already in the yard for a few days (D-034), with valid ISO 6346 numbers; reset and rerun stay deterministic. They are not part of the demo container's order, so K6 is unchanged. Yard containers leaving by truck while the vessel works, and export receiving, are after 1 Dec (D-034). | seed (backlog) |

## 2. Step summary

Status of each step today: ✅ built and tested, 🟡 partly built or only on an open PR, ❌ not built.

| # | Issue #1 step | Trigger | UNS event(s) | MiniTOS end state | OES end state | UI | KG | Historian | Built | Tests |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Pre-advice (inbound, advised on vessel); shown as "discharge list received" with its time (D-031) | Seed (D-031: seeded in the MVP; no live load action) or MiniTOS user creates the container visit | ❌ none today; container-visit status event expected (D-026, TCL-003b) | `ContainerVisit.status=Preadvised`, shown as "advised, on inbound vessel" (D-032) | none | admin / DRF API | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | 🟡 MiniTOS only | ❌ |
| 2 | Vessel arrival (seeded as `Arrived` per D-018; a check of the seeded state, not a live transition), then `InOperation` (D-024) | Seed sets `Arrived` (K4). The visit-transition API (`curl`) is a 200 no-op on the seeded visit; `Arrived` → `InOperation` via the API or the admin panel (TCL-009) | ❌ none today; vessel-visit status events with timestamps expected (D-024, D-026, TCL-003b S2) | `CarrierVisit.status=Arrived` | Sim session running, CHEs born | admin; OES `/simulations/`, `/equipment/` | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | 🟡 API works; forward-only rule not built (D-025, S2) | ❌ (Sparkplug birth ✅) |
| 3 | Discharge QC→TT | Order released; OES `dispatch-next` (token) | `order.released`; `leg_dispatched`×2, `leg_started`, `container_picked_up`(VESSEL), `container_transferred` QC→TT, `leg_completed` | `OnEquipment`/QC01, then `OnEquipment`/TTxx | order `in_progress`, hand-off `transferred` | admin; OES `/equipment/`, `/api/orders/` | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | 🟡 PR #13, #15 | 🟡 per service |
| 4 | TT to yard, RTG stacks | 2nd `dispatch-next` (after H1, Q18) | `leg_dispatched`, `leg_started`, `container_transferred` TT→RTG, `leg_completed`, `container_dropped`, `leg_completed` | `AtYard` in slot; order `complete` | order `complete`, all CHEs free | admin | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | 🟡 PR #13, #17 | 🟡 per service |
| 5 | Yard dwell (clock starts at the QC → TT handover, D-033) | time passes | TBD; hold releases (D-035) | `AtYard` unchanged | none | TBD | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | ❌ | ❌ |
| 6 | Truck booked | scripted driver (T-005, accepted as D-030) | TBD | truck `CarrierVisit` + delivery order TBD | TBD | TBD | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | ❌ | ❌ |
| 7 | Truck arrival | scripted gate-in (T-005) | TBD | truck visit `Arrived` TBD | TBD | TBD | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | ❌ | ❌ |
| 8 | Departure: RTG loads truck | OES dispatch of yard→truck leg | `container_picked_up` (yard), then TBD | `OnEquipment`/RTG01 → on truck TBD | TBD | TBD | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | ❌ | ❌ |
| 9 | Visit closure | scripted gate-out (T-005, D-030) | TBD | container visit `Departed` and `Completed` together; truck visit `Departed` (D-036) | TBD | visit history (TCL-021) | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) | ❌ | ❌ |

## 3. Steps 1–4 (complete)

### Step 1: Pre-advice. The container is inbound, advised on the vessel

Wording (SME review): for an import, terminals say the container is **inbound, advised on vessel**, learned from the line's discharge list. "Pre-advice" is export/truck vocabulary. The enum value `Preadvised` stays; show it to users as "Inbound (advised on vessel V001)".

**Decisions (v2.3):**
- **D-031 (B4):** the pre-advice is **seeded**, shown as **"discharge list received" with a visible time**. No live load action in the MVP. Answers Q1.
- **D-032 (B5):** one container status meaning **"advised, on inbound vessel"** from step 1; no status change at vessel arrival (rename the label, or create visits with that status). Answers Q4. Which of the two (relabel `Preadvised` or seed `OnVessel`) is a team implementation choice, not set here.
- **D-035 (B8):** the demo container carries **separate customs, shipping-line and terminal holds** from step 1, released during the dwell and checked at truck booking and at the gate. Answers Q17. **(not built**, S3–S4)
- **D-026 (A9):** container-visit state changes go on the UNS as business milestones. Answers Q2/Q14 in principle; the schema, milestone names and topic are TCL-003b's (S2 for vessel and container visits).
- **D-041 (B14):** the screens show the ISO 6346 container number and the container's slot on the vessel.

**Preconditions:** K1–K7. In S1 the visit is seeded by TCL-006, so step 1 is a check, not an action (Q1).

| Aspect | Expected | Evidence |
|---|---|---|
| Trigger | One of: `POST /minitos_core/api/bulk-create-container-visits/` with `{"containers":[{…, "category":"I", "ib_carrier":<vessel visit id>}]}`; the TCL-006 seed; or Django admin `/admin/minitos_core/containervisit/add/`. | `minitos_core/urls.py`, `views.py::bulk_create_container_visits`, `serializers.py::BulkContainerVisitSerializer.create` (sets `status=PREADVISED`) [code] |
| UNS/MQTT | **None today.** MiniTOS writes `IntegrationOutbox` rows only for site/configuration and order release (`site_sync.py`, `order_execution.py`), and `signals.py` has no receiver for `ContainerVisit`. Expected: a container-visit event on `scms/v2/{ent}/{site}/tos/…`, with schema id, topic and payload all **TBD** (TCL-003a's acceptance criteria list a "container-visit status" schema, but PR #13 doesn't contain one). Required fields per D-010: `container_visit_id` + ISO `container_number`, `category`, `ib_carrier`, `status`, plus the event timestamp. D-026 requires it; TCL-003b (S2) builds it. | [code] grep of `IntegrationOutbox.objects.create`; D-026 |
| Sparkplug | none (no CHE involved) | — |
| MiniTOS state | `ContainerUnit(container_number=<ISO>, is_in_terminal=False)`. `ContainerVisit(category='I', status='Preadvised', ib_carrier=<vessel visit>, location=<terminal>, current_che=NULL, exception_reason='')`. No `Position` has `container_visit` = this visit. | `models.py::ContainerVisit`, `ContainerUnit`; `exception_reason` [PR #13] |
| OES state | none. OES holds no container state of its own (QA survey gap A2), and no `ExecutionOrder` exists. | `execution/models.py` |
| UI | MiniTOS admin changelist `ContainerVisit` (today shows "Preadvised"; label to show: "advised, on inbound vessel" per D-032, with "discharge list received" and its time per D-031; container number and vessel slot per D-041); DRF browsable API `/minitos_core/locations/{id}/container_visits/`. There is no dedicated visit page (only `landing`, `terminal-browser`, `terminal-editor`, `che_editor` and `berth_plan` templates). | `minitos_site/urls.py`, `minitos_core/templates/` |
| KG | TBD: engine Neo4j with Cypher (D-045); content per D-023, TCL-019 (S3) | D-004, D-045 |
| Historian | TBD: engine TimescaleDB (D-044), TCL-020 (S3). D-024: visit status timestamps are stored here | D-004, D-044 |

**Acceptance checks**
- **AC1.1** Exactly one `ContainerVisit` exists for the ISO number, with `category='I'`, `status='Preadvised'` and `ib_carrier` = the seeded vessel visit.
- **AC1.2** `current_che` is NULL, no `Position` references the visit, and `ContainerUnit.is_in_terminal` is False.
- **AC1.3** OES has no `ExecutionOrder` for the visit.
- **AC1.4** *(not built: TCL-003b, D-026)* One container-visit status event is seen on `scms/v2/#` within the D-037 limit (2 s demo, 5 s per step in CI), it validates against its schema, and it carries both ids (D-010) and its timestamp.
- **AC1.5** *(rerun, D-016)* After a reset and rerun, the visit's pk and state are the same (TCL-006 deterministic ids).
- **AC1.6** *(D-031, D-032, D-041; UI, MVP)* The demo screen shows the container as "advised, on inbound vessel", with "discharge list received" and its time, its ISO 6346 number and its vessel slot.
- **AC1.7** *(D-035; not built, S3–S4)* The container has three separate holds (customs, shipping line, terminal), all active at step 1.

**Coverage:** no test covers the bulk-create endpoint or the `Preadvised` default. There's no event, so there's no contract test. Missing: AC1.1–AC1.7.

### Step 2: Vessel arrival. The vessel arrives with the container

**Scope (D-018, Yael, 31 Oct 2026):** the seed starts the vessel visit as `Arrived` with `ata` set, so this step is a check of the seeded state, not a live transition. The demo script starts from the seeded reset and goes straight to release. The `Created` → `Arrived` transition and its triggers (e.g. no order dispatch before the vessel has arrived) are deferred to after the MVP (backlog). The `curl` below still works and returns a 200 no-op on the seeded visit.

**Decisions (v2.3):**
- **D-024 (A7):** **`Arrived` = the vessel is berthed and moored, not necessarily operational.** The `Arrived` → `InOperation` duration is the terminal-readiness time, an important KPI; simulate a short period, with no berthing-process detail. The timestamps of these status events are tracked per visit (vessel; also truck and train visits) in the UNS and the historian. Answers Q3a and the ATA/operations-start part of Q13. **Still open (A7, Q13):** whether release/dispatch of discharge work is allowed only once the visit is `InOperation`.
- **D-025 (A8):** in the MVP, backward status transitions don't occur: **MiniTOS refuses backward status jumps (forward-only)**, and the refusal has a test (Yael, 2 Nov). Answers Q3b (Q1 in #21). Backward transitions with a tracked reason are post-MVP. The S2 task's acceptance gives the order `Created → Announced → Arrived → InOperation → Complete → Departed` and also refuses **skipped** jumps; D-025's own words cover backward jumps only (Q23). **(not built**: S2 BE task D-025)
- **D-026 (A9):** vessel-visit state changes go on the UNS. TCL-003b (S2) builds the vessel-visit and container-visit status events with timestamps.

**Preconditions:** step 1 passed. The seeded vessel visit is `Arrived` (K4, D-018). A visit in `Created` or `Announced` (created by hand, not by the seed) can still be moved to `Arrived` with the API below.

| Aspect | Expected | Evidence |
|---|---|---|
| Trigger | Vessel visit status → `Arrived` (seeded, D-018), then `InOperation` (D-024). In S2, `InOperation` makes MiniTOS create the discharge orders (TCL-010) and is set from the admin panel (TCL-009); automatic dispatch is TCL-017, S4; whether dispatch must wait for `InOperation` is open (A7, Q13). Any logged-in MiniTOS user (`IsAuthenticated`; HTTP Basic or session, e.g. the superuser from `make create-minitos-admin`). `<visit_id>` is the `CarrierVisit` id shown in admin → Carrier visits:<br>`curl -s -u <admin> -X POST -H 'Content-Type: application/json' -d '{"status":"Arrived"}' "http://localhost:${MINITOS_HTTP_PORT:-8000}/minitos_core/api/visits/<visit_id>/transition-status/"`<br>Responses (code on `main`):<br>- 200 `{success: true, message, visit}`, with `visit.status` = `Arrived`;<br>- 200 no-op if the visit already has that status (nothing is saved);<br>- 400 `{success: false, message, errors}` for a status that is not in `CarrierVisit.STATUS_CHOICES` (`Created`, `Announced`, `Arrived`, `InOperation`, `Complete`, `Departed`); the visit is unchanged;<br>- 400 if `status` is missing, 404 for an unknown id, 401/403 without login.<br>There is **no transition graph** on `main`: any listed status is accepted from any other. D-025 requires forward-only transitions with backward jumps refused (**not built**, S2 BE task). `ata` is not set (Q2 in #21). No UNS event is emitted (D-026 requires one; TCL-003b). The admin panel is TCL-009 (S2), with buttons only for the allowed next statuses. | `minitos_core/views.py::transition_visit_status`, `urls.py`; `test_visit_transition.py` (#21) |
| Simulator start (S1 precondition for step 3) | The Godot session is started from the OES console `/simulations/` with `make run-simulator-runner` running. | `README.md` |
| UNS/MQTT | **None today** for the visit status (same gap as step 1, G1). Expected (D-024, D-026; TCL-003b, S2): one carrier-visit status event per status change (`Arrived`, `InOperation`, …) on `scms/v2/{ent}/{site}/tos/…`, schema and milestone names TBD in TCL-003b, with fields `carrier_visit_id`, `visit_type`, `status` and the status timestamp (e.g. `ata`), so the readiness time `Arrived` → `InOperation` can be computed. | [code]; D-024, D-026 |
| Sparkplug | When the session starts:<br>- **STATE:** `spBv1.0/STATE/{host}` = ONLINE (retained).<br>- **NBIRTH:** carries `Session/Id`, `Site/Id`, `Configuration/Revision`, `Configuration/Checksum` and `bdSeq`, plus [PR #15] `Contract/Version`, `Sim/Seed`, `Sim/StartEpochMs`, `Sim/Timing/QcHoldMinS`/`QcHoldMaxS`/`TtNoShowTimeoutS`/`CommandTtlS`.<br>- **DBIRTH:** one per CHE (`QC01`, `TT01`, `TT02`, `RTG01`), each with `Identity/*`, `State/*`, `Position/*` and `Carrying/ContainerVisitId=0`. | `UNS_CONTRACT.md`; NOTES-TCL-014a |
| MiniTOS state | `CarrierVisit.status='Arrived'`, set by the seed (D-018). The seed also sets `ata` to the seed epoch; the transition API doesn't set `ata` [code]. The container visit stays `Preadvised`: nothing in the code sets `OnVessel`, although the TCL-003a test fixture starts from `OnVessel` (`test_handoff_execution.py:48`). D-032: no container status change at vessel arrival. | `models.py::CarrierVisit` |
| OES state | `SimulationSession` status `running`. `EquipmentState` exists per CHE: `device_connected`, `data_valid`, `is_available`, not `working`/`is_dispatched`, and `idle_since_event_ms` = session start [PR #13]. | `execution/models.py`; `test_handoffs.py::test_dbirth_sets_idle_since_from_session_start` |
| UI | MiniTOS admin `CarrierVisit` ("Arrived"); berth plan `/terminals/{id}/berth-plan/` shows generated visits (it doesn't display status, unverified). OES `/simulations/` shows the session; `/equipment/` lists the 4 CHEs. | templates |
| KG | TBD: engine Neo4j with Cypher (D-045); content per D-023, TCL-019 (S3) | D-004, D-045 |
| Historian | TBD: engine TimescaleDB (D-044), TCL-020 (S3). D-024: visit status timestamps are stored here | D-004, D-044 |

**Acceptance checks**
- **AC2.1** The vessel visit's status is `Arrived`, set through a supported path (the seed per D-018; or the transition API above, or the panel once TCL-009 exists, for a visit that isn't seeded), not a DB edit (MVP DoD 2).
- **AC2.2** *(D-032)* The container visit's status is unchanged by the vessel's arrival: still the single "advised, on inbound vessel" status from step 1.
- **AC2.3** OES shows one running session for the Site. NBIRTH revision/checksum = OES projection revision/checksum. All 4 CHEs are born, available and idle.
- **AC2.4** *(D-024, D-026; not built: TCL-003b)* Each vessel-visit status change (`Arrived`, `InOperation`) produces one status event on the UNS that validates and carries its timestamp, within the D-037 limit (2 s demo, 5 s per step in CI).
- **AC2.5** `POST …/transition-status/` with `{"status":"Arrived"}` returns 200 and the updated visit (covered by #21).
  - **AC2.5a** *(D-025; not built: S2 BE task)* A backward jump (e.g. `Departed` → `Arrived`, or `InOperation` → `Arrived`) is refused with a clear error, and the visit is unchanged. The HTTP code and error body are the BE task's (not set by D-025). Today the code accepts `Departed` → `Arrived` with 200.
  - **AC2.5b** *(S2 task acceptance, not D-025's wording; Q23)* A skipped forward jump (e.g. `Arrived` → `Departed`) is refused the same way.
- **AC2.6** *(D-024)* Moving the seeded visit `Arrived` → `InOperation` succeeds through a supported path (API or TCL-009 panel), and both status timestamps are recorded (UNS event; historian in S3), so the readiness time is computable. Whether release/dispatch before `InOperation` is refused is **not** asserted (A7, Q13 open).

**Coverage:**
- Sparkplug NBIRTH/DBIRTH: covered in OES `execution/tests.py` and `test_handoffs.py` [PR #13], and in Godot `test_headless_sim.gd::_test_node_birth_metrics` [PR #15].
- Visit status transitions: MiniTOS `minitos_core/test_visit_transition.py` (#21): `Arrived` → 200 and saved, same status → 200 no-op, unknown status → 400, missing status/unknown id/anonymous → 400/404/401-403.
- Missing: AC2.2, AC2.4, AC2.5a/b (D-025 task), AC2.6, and AC2.1 run against a live stack.

### Step 3: Discharge. The TT is sent to the QC, and the QC discharges the container onto the TT

D-005 and D-012 set the flow. The TT drives to the quay. The container *appears* on the QC; there is no lift from the vessel. The QC holds it for a seeded 10–30 s (whole seconds) and then deposits it on the TT, waiting for the TT if it's late. T-006: OES dispatches QC and TT together with a shared `HandoffId`.

**Decisions (v2.3):**
- **D-039 (B12):** realistic crane timings (QC ~90–120 s per cycle) in the **live demo only**; tests and CI use **fast** timings; the profile is a sim-init parameter. This changes D-012's 10–30 s hold for the live demo. How the realistic cycle splits into hold/move phases, and the fast profile's exact values, are not specified (Q22). QC hoist/trolley motion is after 1 Dec.
- **D-033 (B6):** the container counts as **in the terminal at the QC → TT handover**; terminal custody and the dwell clock start there. Answers Q6 and Q15 (today `is_in_terminal` turns True only at the yard drop, G5).
- **D-040 (B13):** "waiting for TT" is shown **separately from idle** and doesn't count as idle; the live demo runs at real time (×1). **Still open (B13):** whether to add the single `NO_TT` delay-code field (Q19).
- **D-029 (B2):** MVP telemetry is position plus a **"twistlocks locked"** signal, e.g. when the QC handles the container. **(not built**, scheduled after S2; the contract name is TBD)
- **D-026 (A9):** container-visit and equipment state changes go on the UNS (S2: container visits; equipment states in S3).
- **D-037 (B10):** each step reaches a consistent MiniTOS and OES state within 2 s in the demo and 5 s per step in CI, with strict event order.

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
| Trigger | OES API (S1 is manual; automatic dispatch is TCL-017, S4). Every `/api/` call needs the `X-OES-Token` header: the DRF default permission is `HasOESServiceToken`, and compose sets `OES_API_TOKEN` (from `.env`, else the compose default). Without it the call returns 403. Load the token from `.env` without writing it down: `set -a; . ./.env; set +a`.<br>1. Find the **OES** order id (the `id` field; the MiniTOS order id is `tos_order_id`, a different number):<br>`curl -s -H "X-OES-Token: $OES_API_TOKEN" "http://localhost:${OES_HTTP_PORT:-8010}/api/orders/?status=released"`<br>2. Dispatch:<br>`curl -s -X POST -H "X-OES-Token: $OES_API_TOKEN" -H 'Content-Type: application/json' -d '{}' "http://localhost:${OES_HTTP_PORT:-8010}/api/orders/<oes_id>/dispatch-next/"` → HTTP 201 (or use the TCL-006 step command if it wraps this).<br>A second call before H1 transfers sends **no new command**: HTTP 200 with `dispatched: false`, `code` and `detail` (TCL-028). It returns 409 `simulation_not_running` if the session is stopped, 409 for other refusals and 400 for a bad body or `equipment_id`. | `execution/permissions.py::HasOESServiceToken`; `terminal_oes/settings.py` `REST_FRAMEWORK`; `compose.yaml` `OES_API_TOKEN`; `execution/views.py::ExecutionOrderViewSet` (`get_queryset` `status` filter, `dispatch_next`); `execution/models.py::ExecutionOrder.tos_order_id`; `dispatch.py::dispatch_next_operation` [PR #13]; TCL-028 `views.py::dispatch_next` [PR #20]; `test_qc_and_tt_are_dispatched_together_with_one_handoff_id` |
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
| UI | MiniTOS admin `ContainerVisit`: "On Equipment" with current CHE = QC01, then TTxx. OES `/equipment/`: QC/TT Working/Dispatched flags and raw telemetry. OES API `/api/orders/{id}/`, `/api/commands/`. Godot stays headless with **no 3D view** (D-028); equipment real state is shown in the OES equipment-state view (MVP, not built), where a QC waiting for its TT shows "waiting for TT", not idle (D-040). Demo screens show the container number and vessel slot (D-041). | templates; D-028, D-040, D-041 |
| KG | TBD: engine Neo4j with Cypher (D-045); content per D-023, TCL-019 (S3) | D-004, D-045 |
| Historian | TBD: engine TimescaleDB (D-044), TCL-020 (S3). D-024: visit status timestamps are stored here | D-004, D-044 |

**3x. Exception path: TT no-show (D-013, D-017)**

If the TT hasn't arrived N s after the hold ends (N from NBIRTH `Sim/Timing/TtNoShowTimeoutS`, else `SIMULATOR_TT_NO_SHOW_TIMEOUT_S`, else 300; configurable per D-017), the QC publishes `handoff_timed_out` once. OES then:
- records `Handoff.status='exception'`;
- emits `handoff_exception` with `reason:"SIMULATOR_FAILURE"`, `detail:"no TT within <N> s"` (e.g. "no TT within 300 s" with the default), the hand-off ids and the names;
- sends cancel DCMDs (`Command/Type=cancel`, `CancelCommandId`, `Reason=SIMULATOR_FAILURE`) for both legs.

This is a **simulator failure marker, not an operational recovery** (SME review: a real terminal would send another TT). **D-038 (B11):** the container goes to a **normal block** (the process doesn't change; no separate exception area), is **marked as a simulator failure** (`SIMULATOR_FAILURE`, D-017) and is **excluded from the KPIs**. Answers Q16. **D-027 (A10):** the same logic applies to every CHE, and the time-out is a sim-init parameter. **D-043 (B16):** the MiniTOS user sees the failure reason and details; when the simulator gives no reason, OES fills in a default code. Which code is not stated (Q20); today TCL-028 uses `UNSPECIFIED` for a failed result with no reason, a team choice and not a decision.

MiniTOS then moves the visit to a free `BLOCK01` slot (a normal block, D-038), sets `AtYard` and `exception_reason`/`exception_detail`, completes the order **by exception** (`complete`), cancels the open legs and frees both CHEs.

Evidence: `handoffs.py::no_show_detail`, `apply_handoff_timed_out` and `order_execution.py::relocate_container_after_exception` [PR #13]; `test_tt_no_show_raises_one_exception_and_cancels_both_legs`, `test_no_show_exception_relocates_to_block01_flagged`; golden `handoff/03_handoff_exception_simulator_failure.json` [PR #14]; Godot `_test_sim_tt_no_show_then_cancel` [PR #15].

**Acceptance checks**
- **AC3.1** The released payload validates (schema TBD for `order.released`; see gap G4). `container_number` = the visit pk and `container_unit_number` = the ISO number.
- **AC3.2** One `dispatch-next` call produces exactly 2 DCMDs: QC `qc_discharge` (giver) and TT `execute_order_leg` (receiver), with the same `HandoffId`, crossed `CounterpartCheId`, and a sim-time `ExpiresAtMs`. The RTG op stays `planned`. A second call before H1 transfers sends **no new DCMD**, and the response says why (200 `dispatched:false` with `code`/`detail`, TCL-028).
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
- **AC3.6** Sim time from appear to deposit (compressed time; today's fast timings) is a whole number in [10, 30] if the TT was waiting, else the deposit happens at TT ready (D-012). Same seed, same hold sequence (D-016). See AC3.14 for the D-039 timing profiles.
- **AC3.7** No `CommandResultReceipt` is `rejected`, and no MiniTOS `IntegrationInbox` row is `failed`.
- **AC3.8** No-show (fields checked in the #13 code, now on `main`):
  - **AC3.8a** OES `Handoff`: `status='exception'`, `exception_reason='SIMULATOR_FAILURE'`, `exception_detail='no TT within <N> s'`.
  - **AC3.8b** OES `ExecutionOrder`: `status='complete'`, `failure_reason='SIMULATOR_FAILURE'`. Open `ExecutionOperation`s and `DispatchAssignment`s are `cancelled` with `failure_reason='SIMULATOR_FAILURE'`, and cancel DCMDs are sent.
  - **AC3.8c** MiniTOS `ContainerVisit`: `status='AtYard'` in exactly one `BLOCK01` slot, `current_che` NULL, `exception_reason='SIMULATOR_FAILURE'`, `exception_detail` = the event's `detail`.
  - **AC3.8d** MiniTOS `Order`: `status='complete'` with the open legs `cancelled`. The MiniTOS `Order` has **no** reason field; the reason lives on the visit.
  - **AC3.8e** The QC and TT are available and not dispatched in both systems.
  - **AC3.8f** *(D-038; KPIs not built)* The no-show container is excluded from every KPI: the KPIs of a run with a no-show equal those of the same run without that container.
  - **AC3.8g** *(D-043; not built, S2 stretch)* The MiniTOS UI shows the failure reason and detail on the visit. A failed result with no reason gets OES's default code (value open, Q20).
- **AC3.9** The duplicate DCMD and duplicate result (QoS 1) are each applied once (`Command/Result/Id`, `event_id`).
- **AC3.10** *(D-033; not built)* At the QC → TT transfer (AC3.4 step 6), the container becomes "in the terminal" (`ContainerUnit.is_in_terminal=True`) and its dwell clock starts at the transfer's event time. Today it turns True only at the yard drop (G5).
- **AC3.11** *(D-037)* After each UNS event in AC3.4, MiniTOS and OES show a consistent state within 5 s in CI (2 s in the demo); the order of AC3.4 is checked strictly.
- **AC3.12** *(D-040)* While the QC holds a container and its TT hasn't arrived, the QC's state is "waiting for TT", not idle, and that time isn't counted as idle (UI and KPIs; not built).
- **AC3.13** *(D-029; not built)* When the QC handles the container, a "twistlocks locked" signal is published for QC01.
- **AC3.14** *(D-039; Sim params task)* With the `fast` profile, AC3.6 applies with the fast profile's hold range (Q22). With the `realistic` profile (live demo only), the QC cycle is ~90–120 s. CI never runs `realistic`.

**Coverage:**
- **Per service, on open PRs:**
  - OES `test_handoffs.py` (AC3.2–3.4, 3.7–3.9) [PR #13]
  - MiniTOS `test_handoff_execution.py` (AC3.5, 3.8) [PR #13]
  - contract tests `test_contract_execution_events.py` / `test_contract_handoff_events.py` in both services (AC3.4 shape) [PR #14]
  - Godot `test_headless_sim.gd` (`_test_qc_state_machine_*`, `_test_sim_qc_tt_*_end_to_end`, `_test_sim_replay_same_seed`; AC3.6 per service) [PR #15]
  - D-012/D-013 acceptance in `tests/requires-TCL-014a/test_acceptance_d012_d013.gd` [PR #14], which still isn't wired into `make test-godot` on `main`.
- **Missing:**
  - (a) A cross-service run (compose: MiniTOS + OES + HiveMQ + Godot) asserting AC3.4/3.5 on the real broker. That's TCL-007, S2.
  - (b) The released payload of a real 3-leg discharge order built by MiniTOS. The tests build the legs by hand.
  - (c) An `order.released.v1` JSON Schema and goldens (G4).
  - (d) Shared Python/GDScript golden vectors for the `qc_discharge` DCMD and the `handoff_completed` result.
  - (e) The hold distribution across seeds (QA survey cases D012-3/4: holds stay in [10, 30] and vary with the seed).

### Step 4: TT takes the container to the yard. The TT drives to the yard location, and the RTG takes the container and places it in the yard

**D-027 (A10) answers O7/O8:** every CHE, the RTG included, uses the same simulation-failure logic as D-017: on time-out, record `SIMULATOR_FAILURE`, let the flow continue, note it was a simulation issue; the time-out is a user-adjustable sim-init parameter. Built by the S2 "Sim params" task (one time-out per CHE type, default 300 s, published in NBIRTH). **D-039:** realistic RTG timings (~60–120 s) in the live demo only; fast timings in tests and CI. **D-033:** the container is already in the terminal from the QC → TT transfer.

**S1 timeout caveat (until the Sim params task lands):** in S1 there is **no timeout** while the TT waits at the block for the RTG (contract §3.3a; `SIMULATOR_RTG_NO_SHOW_TIMEOUT_ENABLED` / `Sim/Timing/RtgNoShowTimeoutEnabled` default `false` [PR #17]). If H2 never completes, the run stalls at `waiting_for_handoff` with no exception. Stop the session and reset (`make scenario-reset SCENARIO=import_v1`).

**Preconditions:** step 3 passed: `Handoff(H1)` is `transferred`, and the TT carries the container. RTG01 is idle in `BLOCK01`. The RTG leg's destination slot is free.

| Aspect | Expected | Evidence |
|---|---|---|
| Trigger | The same `curl … /api/orders/<oes_id>/dispatch-next/` with `X-OES-Token` as in 3b, after H1 has transferred (Q18; manual until TCL-017). | `dispatch.py::_plan_dispatch` (chained) [PR #13] |
| Sparkplug | **DCMD** `…/TTxx`:<br>- `Command/Type=assign_handoff`, `HandoffRole=giver`<br>- `HandoffId=H2`, `CounterpartCheId=RTG01`, `TransferPositionId`<br>- `LegId` = the TT leg, `ContainerVisitId`<br><br>**DCMD** `…/RTG01`:<br>- `Command/Type=execute_order_leg`, `HandoffRole=receiver`<br>- `DestinationPositionId`, `DestinationPositionCode`, `DestinationInventoryPositionId` (the MiniTOS slot), `DestinationLatitude`/`Longitude`<br><br>**Results:**<br>- TT: `arrived_at_destination`, `waiting_for_handoff`<br>- RTG: `acknowledged`, `started`, `handoff_completed` (receiver, the pick)<br>- TT: `handoff_completed` (giver, sent on the assign id), then `completed` (TT leg)<br>- RTG: seeded hold 10..30 s (compressed time), `container_dropped` echoing one `Command/Result/PositionId` (the inventory id) plus `Command/Result/PositionCode` (O11, rev 9.3), then `completed`; under D-039 this hold follows the timing profile (realistic RTG ~60–120 s, live demo only)<br>The O12 pre-send consistency guard (OES validates the destination ids before sending) isn't built yet (backlog, S2). | `UNS_CONTRACT.md` [PR #13]; NOTES-TCL-014b [PR #17] |
| UNS | `leg_dispatched`(3, RTG01), `leg_started`(3), `container_transferred` (TT→RTG01), `leg_completed`(2, TT), `container_dropped`(3, RTG01, `position_id`=slot), `leg_completed`(3) | [run]; golden `handoff/02_container_transferred_tt_to_rtg.json`, `04_container_dropped.json` [PR #14] |
| MiniTOS | After the transfer: `current_che=RTG01`. After the drop: `Position(slot).container_visit=<visit>`, `ContainerVisit.status='AtYard'`, `current_che=NULL`, lat/lon = the slot's operational position, `ContainerUnit.is_in_terminal=True` (already True since the QC → TT transfer under D-033). All legs `complete`, `Order.status='complete'`, `completed_at`/`atc` set. TT and RTG available again. | `order_execution.py::drop_container`, `apply_execution_event` (`leg_completed`) |
| OES | `Handoff(H2)='transferred'`. Ops 1–3 `complete`; `ExecutionOrder='complete'`. Every command `complete`, no `rejected` receipts. QC, TT and RTG available, not dispatched. | `test_qc_tt_rtg_import_completes_with_one_custody_event_per_handoff` |
| UI | MiniTOS admin `ContainerVisit` "At Yard"; admin `Position` shows the visit in the slot. Terminal browser `/terminal-browser/` (whether it shows occupancy is unverified). OES `/equipment/`: all CHEs idle. | templates |
| KG | TBD: engine Neo4j with Cypher (D-045); content per D-023, TCL-019 (S3) | D-004, D-045 |
| Historian | TBD: engine TimescaleDB (D-044), TCL-020 (S3). D-024: visit status timestamps are stored here | D-004, D-044 |

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
- **AC4.3** MiniTOS: the visit is in exactly one slot (the planned one), `AtYard`, with no CHE. No other slot references it, and the slot isn't double-booked. `is_in_terminal=True` (from AC3.10 on, D-033).
- **AC4.4** OES and MiniTOS both report the order `complete`, and all 4 CHEs are available.
- **AC4.5** The Godot RTG drops into the slot OES sent, not a seeded dummy slot. That fallback is only for when no id is sent (O6). Until the O12 guard exists, this relies on MiniTOS sending consistent destination ids.
- **AC4.6** Rerun from reset: the same states in the same order for steps 1–4 (D-016), with the same TT choice and the same hold sequence.
- **AC4.7** *(D-027, D-038; not built: Sim params task)* RTG/TT no-show at the block: after the RTG time-out (sim-init parameter, default 300 s), the hand-off is recorded as `SIMULATOR_FAILURE`, the order completes by exception and the run doesn't stall; the container ends in a normal block slot, marked, out of KPIs. Which CHE detects the time-out and how MiniTOS places the container are for TCL-011 (design), not set by D-027. Until it lands, a stall means stop and reset (see the caveat above).
- **AC4.8** *(D-027)* Changing the time-out parameter at sim init changes when the no-show fires (at the quay and at the block).
- **AC4.9** *(D-037)* AC4.2's order is strict, and each step is consistent in both systems within 5 s in CI (2 s demo).

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
| 5 | Yard dwell | Time passes; the dwell clock started at the QC → TT handover (D-033). How the demo container's dwell is shown is **open (B7, Q8)**; the live demo runs at real time (D-040) | Hold releases (customs, line, terminal) during the dwell (D-035); event names TBD | `AtYard`, slot unchanged; holds released (D-035). Seeded older yard containers (K8, D-034) | none | Nothing to build except the dwell display (open) | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) |
| 6 | Truck booked | A scripted scenario driver (T-005 accepted for the MVP, D-030; TCL-024) or a MiniTOS user creates the truck visit and booking; holds are checked at booking (D-035) | TBD (TCL-003b) | `CarrierVisit(visit_type='Truck', status='Created')`, `ContainerVisit.ob_carrier` = truck visit; a delivery order (`order_type='OT'`) TBD | none | The `CarrierVisit` Truck type exists; there's no truck-visit/gate model (#9) | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) |
| 7 | Truck arrival | Scripted gate-in (D-030); holds checked at the gate (D-035) | TBD (TCL-003b; truck-visit status with timestamp, D-024/D-026). The truck isn't a Sparkplug device under T-005. | truck visit `Arrived` TBD | Delivery order released → `ExecutionOrder` TBD | none | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) |
| 8 | Departure: RTG loads the truck | OES dispatch of the yard→truck RTG leg (stays a real Godot command, D-030) | DCMD RTG `execute_order_leg`; `container_picked_up` (from the yard slot), then the drop onto the truck: TBD. There's no truck position type, and `drop_container` needs a yard `Position`. | `pickup_container` clears the slot → `OnEquipment`/RTG01; after that TBD | TBD | RTG yard→truck is out of scope in TCL-014b | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) |
| 9 | Visit closure | Scripted gate-out (D-030) | TBD (TCL-003b). Whether the visit end gets its own event is not stated (Q21; D-026 puts business milestones on the UNS) | At gate-out, `ContainerVisit` is set to `Departed` **and** `Completed` together (D-036); `ContainerUnit.is_in_terminal=False`; truck visit `Departed` (D-036) | TBD | Nothing sets `Departed`/`Completed` from an event | TBD (Neo4j, D-045) | TBD (TimescaleDB, D-044) |

These issue-level requirements apply after step 9:
- **R1:** the final visit history can be inspected (DoD 4, TCL-021).
- **R2:** the scenario reruns from the known state (DoD 1, 5; TCL-006, merged in #24).
- **R3 (D-042, D-038, D-040):** demo KPIs, MVP, S3–S4, not built. Headline: QC cycle time and moves per hour, QC waiting for TT (separate from idle, D-040), TT cycle time, yard dwell, truck turn time; **OEE is also shown** (D-042). No-show containers are excluded from every KPI (D-038). Computed from event (sim) time. KPI and OEE definitions are still to be written (team note on D-042); the Arrived → InOperation terminal-readiness time is also a KPI (D-024).
- **R4 (D-024):** visit status timestamps for vessel and truck visits (train visits post-MVP) are on the UNS and in the historian, so durations between statuses can be computed.

## 5. Test coverage map

| Check | Exists (branch) | Missing / owner |
|---|---|---|
| AC1.* pre-advice | — | MiniTOS API test for bulk create; event contract test (TCL-003b, D-026); UI check AC1.6; holds AC1.7 (D-035) |
| AC2.1/2.5 visit transition | MiniTOS `test_visit_transition.py` [#21] | Forward-only refusal test AC2.5a/b (S2 BE task, D-025); live-stack run; panel (TCL-009) |
| AC2.4, AC2.6 visit status events + timestamps | — | TCL-003b contract test (D-024, D-026); TCL-007 e2e |
| AC2.3 session birth | OES `tests.py`, `test_handoffs.py` [#13]; Godot `_test_node_birth_metrics` [#15] | Cross-service (TCL-007) |
| AC3.1 released payload | `test_released_payload_carries_*` [#13] | `order.released.v1` schema + goldens; a real 3-leg discharge order (TCL-006/TCL-010) |
| AC3.2–3.4, 3.7–3.9 | OES `test_handoffs.py` [#13]; contract tests [#14] | Broker-level run |
| AC3.5 custody | MiniTOS `test_handoff_execution.py` [#13]; `test_acceptance_rules.py` [#14] | Whole-sequence consumer test |
| AC3.6 hold | Godot `test_headless_sim.gd` [#15]; `requires-TCL-014a/test_acceptance_d012_d013.gd` [#14, not wired] | Wire it into `make test-godot` / TCL-029 CI |
| AC3.8a–e no-show | OES, MiniTOS [#13]; Godot [#15]; golden [#14] | Cross-service |
| AC4.1–4.4 | OES happy path [#13]; goldens [#14]; Godot TCL-014b [#17] | MiniTOS whole-sequence; cross-service |
| AC4.5 RTG slot | Godot TCL-014b [#17] | Broker-level; O12 guard (S2) |
| AC3.8f, R3 KPIs | — | KPI tests once KPIs exist (S3–S4, D-038, D-042) |
| AC3.10 custody at QC → TT | — | MiniTOS test (container status rework, D-033) |
| AC3.11, AC4.9 timing limits | — | TCL-007 CI e2e, ≤5 s per step (D-037) |
| AC3.12–3.14 waiting for TT, twistlocks, timing profile | — | OES view (D-028/D-040); telemetry (D-029); Godot tests for both profiles (Sim params, D-039) |
| AC4.7–4.8 block no-show, time-out parameter | — | Godot + OES tests (Sim params task, D-027) |
| AC1.5, AC4.6 rerun | OES `test_handoffs.py::test_d016_same_seed_same_states_in_the_same_order` [#13]; `execution/test_acceptance_rules.py::test_d016_rerun_produces_same_state_sequence_not_same_messages` [#14] | Full stack (needs TCL-006) |
| Steps 5–9 | — | TCL-003b, 018, 024, 022b |
| KG / historian per step | — | TCL-019 (Neo4j, D-045), TCL-020 (TimescaleDB, D-044), S3 |

**Gaps found while writing this spec** (not fixed here; for the Tech Lead to assign):
- **G1:** MiniTOS emits no UNS event for container-visit or carrier-visit status (steps 1, 2, 9). D-024/D-026 require them; TCL-003b builds the vessel-visit and container-visit events in S2.
- **G2:** *(resolved by TCL-032, #21)* the transition views referenced undefined names and returned 500. On `main` they validate the status against `STATUS_CHOICES` only; transition rules and `ata`/`atd` are open questions Q1/Q2 in #21.
- **G3:** Nothing sets `ContainerVisit.status='OnVessel'` or `CarrierVisit.atd`. `CarrierVisit.ata` is set only by the seed (D-018); the transition API doesn't set it.
- **G4:** There's no JSON Schema or golden for `scms.order.released.v1` (TCL-005 covers only `scms.execution.event.v1`).
- **G5:** `ContainerUnit.is_in_terminal` turns True only at the yard drop. D-033 sets it at the QC → TT handover (AC3.10); not built (container status rework, S3–S4).
- **G6:** No MiniTOS path creates the 3-leg discharge order (QC→TT→RTG). The tests build it by hand.

**Follow-ups from the SME review** (not spec changes):
- **F1 (S2):** rename the released-payload field `container_number` (it holds the visit pk) to `container_visit_id`, and use `container_number` for the ISO number. The SME has logged it for S2.
- **F2:** the test fixtures and goldens from #13/#14 (now on `main`) use `MSCU1234565`, whose ISO 6346 check digit is wrong; the valid number is `MSCU1234566`. `ContainerUnit` doesn't validate the check digit today.

## 6. Decisions applied and open questions

### 6.1 Decision mapping (v2.3, TCL-001c)

Decisions are quoted from the team vault note `03 Decisions.md` (Yael's answers to groups A and B of the decision sheet, 1 and 2 Nov 2026). "Not built" means the spec now states the decided behaviour, but no code has it yet.

| Decision | Sheet | What Yael decided (short) | Where this spec changes | Answers |
|---|---|---|---|---|
| D-018 | Y1 | Seeded vessel visit starts `Arrived`, `ata` = seed epoch; Created → Arrived deferred | K4, step 2, G3 (v2.2) | Y1 (PR #24 review) |
| D-024 | A7 | `Arrived` = berthed and moored, not necessarily operational; Arrived → InOperation = terminal-readiness KPI; status timestamps per visit on the UNS and historian | K4; §2 row 2; step 2 decisions, trigger, UNS, AC2.4, AC2.6; R3, R4 | Q3a; Q13 in part (dispatch gating still open) |
| D-025 | A8 | MVP: no backward transitions; MiniTOS refuses backward jumps (forward-only), with a test | §2 row 2; step 2 decisions, trigger, AC2.5a/b; §5 | Q3b (Q1 in #21) |
| D-026 | A9 | Business milestones / state changes on the UNS for visits, container visits, work queues, yard positions, equipment | step 1 UNS, AC1.4; step 2 UNS, AC2.4; step 3 decisions; G1 | Q2 (in principle; schema per TCL-003b), Q14 |
| D-027 | A10 | Every CHE can have a simulation failure, same logic as D-017; flow continues; time-out is a user-adjustable sim-init parameter | §0; K7; step 3x; step 4 caveat, AC4.7, AC4.8 | O7/O8 |
| D-028 | B1 | No Godot 3D view; equipment real-state view in OES, in the MVP | §0; step 3d UI | Q11 |
| D-029 | B2 | MVP telemetry: position + "twistlocks locked" only; the rest after 1 Dec | §0; step 3 decisions, AC3.13 | — (T-004 dispatch-liveness rule stays proposed) |
| D-030 | B3 | Truck stays scripted (T-005 accepted); RTG → truck stays a real Godot command | §2 rows 6, 9; §4 steps 6–9 | T-005 |
| D-031 | B4 | Pre-advice is seeded, shown as "discharge list received" with a visible time | §2 row 1; step 1 decisions, UI, AC1.6 | Q1 |
| D-032 | B5 | One container status "advised, on inbound vessel" from step 1; no change at vessel arrival | K5; §2 row 1; step 1 UI, AC1.6; step 2 MiniTOS state, AC2.2 | Q4 |
| D-033 | B6 | Container is "in the terminal" at the QC → TT handover; custody and dwell clock start there | §2 row 5; step 3 decisions, AC3.10; step 4 MiniTOS, AC4.3; G5 | Q6, Q15 |
| D-034 | B7 | Seed has some yard containers already there a few days (MVP); busy-terminal start after 1 Dec | K8; §4 step 5 | Q8 in part (dwell display still open) |
| D-035 | B8 | Separate customs, line and terminal holds from step 1, released during dwell, checked at booking and gate | K5; step 1 decisions, AC1.7; §4 steps 5–7 | Q17 |
| D-036 | B9 | At gate-out the container visit is `Departed` and `Completed` together; truck visit `Departed` | §2 row 9; §4 step 9 | Q9 (own event: Q21) |
| D-037 | B10 | Consistent state within 2 s (demo) and 5 s per step (CI); strict event order | §0; AC1.4, AC2.4, AC3.11, AC4.9 | Q10 |
| D-038 | B11 | No-show container to a normal block, marked `SIMULATOR_FAILURE`, out of KPIs | step 3x, AC3.8f; AC4.7; R3 | Q16 |
| D-039 | B12 | Realistic crane timings (QC ~90–120 s, RTG ~60–120 s) in the live demo only; fast in tests/CI; profile is a sim-init parameter; hoist/trolley motion after 1 Dec | header term; §0; K7; step 3 decisions, AC3.6, AC3.14; step 4 | — (changes D-012's hold for the live demo) |
| D-040 | B13 | "Waiting for TT" shown separately, not idle; live demo at real time (×1) | §0; step 3 decisions, UI, AC3.12; R3 | — (`NO_TT` field still open: Q19) |
| D-041 | B14 | Screens show the ISO 6346 container number and its vessel slot | §0; step 1 decisions, UI, AC1.6; step 3d UI | — |
| D-042 | B15 | Ops KPIs as headline, OEE shown too | R3 | — |
| D-043 | B16 | MiniTOS shows failure reason and details; OES fills in a default code when none is given | step 3x, AC3.8g | — (which code: Q20) |
| D-044, D-045 | — | Historian TimescaleDB; KG Neo4j with Cypher (context, not group A/B) | header; KG/historian cells | Q12 in part |

### 6.2 Open questions for Yael

These go through the Tech Lead. QA doesn't decide them. Each has the SME's recommendation (SME review of TCL-001, 27 Oct, team notes); Yael decides. Numbers are kept stable so earlier references still work (Q18 is the contract term defined in the header, so new questions start at Q19).

| Q | Status | Question | SME recommendation |
|---|---|---|---|
| **Q1** Seed vs live step 1 | **Answered: D-031** (seeded, "discharge list received" with a visible time; no live load action in the MVP). | — | Seed it, and present it as "discharge list received" with a visible timestamp. A live load action can come later. |
| **Q2** UNS events for steps 1–2 (and 9) | **Answered in principle: D-024, D-026** (status changes with timestamps go on the UNS). Sprint: TCL-003b in S2 for vessel and container visits; truck/work-queue/yard/equipment in S3. Granularity and milestone names: TCL-003b (TL/BE). | — | Per-entity status-changed events (`container_visit`, `carrier_visit`) carrying industry milestone names; see Q14. |
| **Q3a** What counts as "vessel arrival" (domain) | **Answered: D-024** (`Arrived` = berthed and moored, not necessarily operational; then `InOperation`). | — | Show both: `Arrived` (ATA), then `InOperation` (ATB/operations start). See Q13. |
| **Q3b** Which visit transitions are allowed | **Answered: D-025** (forward-only in the MVP; backward jumps refused, with a test). Skipped forward jumps: Q23. | — | Decides AC2.5a. |
| **Q4** Container status on the vessel | **Answered: D-032** (one status "advised, on inbound vessel"; no change at arrival). | — | No transition at arrival. Use one pre-discharge status meaning "inbound, advised on vessel" from step 1 (rename the label or create visits as `OnVessel`), and align the TCL-003a fixture with it. |
| **Q5** Discharge order shape | **Answered** by T-006 and contract rev 9.3 (TL statement Yael can object to): one order with 3 legs QC→TT→RTG. Creator: TCL-006 step command in S1, TCL-010 in S2. | — | Agrees: one order with 3 legs; in S2 generate it from a "QC01 discharge work queue". |
| **Q6** When is it "in terminal" | **Answered: D-033** (at the QC → TT handover). | — | At the QC→TT transfer (discharge confirmation). Keep `OnEquipment`/TT for location. See Q15. |
| **Q7** D-013 "ready" | **Merged into D-013**: the same open item as the Tech Lead's reading of D-013 (count from the end of the D-012 hold). Not asked separately. | — | Confirm: count from the end of the hold; treat the timeout as a simulator-failure detector only. |
| **Q8** Yard dwell | **Partly answered: D-034** (seeded older yard containers in the MVP). **Open (B7):** how the demo container's own dwell is shown (e.g. the sheet's "jump about 3 days and show dwell so far"); with the live demo at real time (D-040), a multi-day dwell can't pass live. | How is the demo container's dwell shown in the demo? | Jump sim time by about 3 days; show "dwell so far" from the discharge time; emit the customs/line release events during dwell (Q17). |
| **Q9** Visit closure | **Answered: D-036** (container visit `Departed` and `Completed` together at gate-out; truck visit `Departed`). Own event: Q21. | — | `Departed` at gate-out with its own status-changed event; truck visit `Departed`; `Completed` only as a later administrative close. |
| **Q10** Timing tolerance | **Answered: D-037** (2 s demo, 5 s per step in CI, strict order). The QA/TL proposal (5 s / 10 s) is dropped. | — | Tighter: demo ≤ 2 s, CI ≤ 5 s; assert ordering strictly and latency loosely. |
| **Q11** UI for S1 | **Answered: D-028** (no Godot 3D view; equipment real state in an OES view, in the MVP). | — | Admin and OES console are acceptable for the S1 internal review only; fix the display labels to industry terms now; the ex-colleague demo needs the visit timeline (TCL-021). |
| **Q12** KG and historian | **Partly answered:** engines D-044 (TimescaleDB) and D-045 (Neo4j with Cypher); KG minimum per D-023. **Open:** what each step shows in the KG and historian (TCL-019/020, S3). | What must each step show in the KG and the historian? | KG: current-state edges plus immutable event nodes per milestone. Historian: CHE states, waiting times and milestone events keyed on sim time. |
| **Q13** ATA/ATB split and dispatch gating (SME) | **Partly answered: D-024** (`Arrived` and `InOperation` are separate statuses with tracked timestamps). **Open (A7):** whether release/dispatch of discharge work is allowed only in `InOperation` (and an AC for it). The S2 plan asks Yael by Wed 4 Nov. | Should release/dispatch be allowed only once the visit is `InOperation`? | Yes, before the 1 Dec demo. |
| **Q14** Business milestone events (SME) | **Answered: D-026.** Milestone names: TCL-003b. | — | Yes; it covers steps 1, 2 and 9 and the "discharged"/"in yard" views in one schema. |
| **Q15** Custody and dwell start at the QC→TT transfer (SME) | **Answered: D-033.** | — | Yes: that is when the box is discharged. |
| **Q16** No-show box to an exception area outside KPIs (SME) | **Answered: D-038** (normal block, no exception area; marked simulator failure; excluded from KPIs). | — | Yes to all three. (Yael chose a normal block instead of an exception area.) |
| **Q17** Customs and line holds (SME) | **Answered: D-035** (separate customs, line and terminal holds). | — | Yes: the holds are what make the import story real. |
| **Q19** `NO_TT` delay code (B13) | **Open.** D-040 decides "waiting for TT" is shown separately and not idle; the single `NO_TT` delay-code field is not decided. | Add a single `NO_TT` delay-code field? | — |
| **Q20** D-043 default code | **Open** (team will propose; S2 plan). D-043 doesn't name the code. Today TCL-028 sets `UNSPECIFIED` for a failed result with no reason (team choice). | Which default code does OES fill in? | — |
| **Q21** Visit-end event (D-036) | **Open.** D-036 doesn't say whether the end of the visit gets its own event. | Does gate-out emit its own visit-end event? | Yes (Q9 recommendation). |
| **Q22** Timing profile values (D-039) | **Open** (team design, Sim params task). D-039 doesn't say how the realistic cycle splits into hold/move phases, or what the fast profile's values are (today's 10–30 s hold?). | — | — |
| **Q23** Skipped forward jumps (D-025) | **Open for confirmation.** The S2 task acceptance refuses skipped jumps (e.g. `Arrived` → `Departed`); D-025's words cover backward jumps. | Are skipped forward jumps refused too? | — |
| **Q24** CI sim speed (D-040) | **Open** (team proposal: CI may run faster than real time; D-040 is about the live demo). | — | — |

## 7. What this spec did not verify

- No Docker on QA's box: nothing here was run through compose, HiveMQ or a live Godot process.
- The [run] event order comes from TCL-003a's own OES test scenario (`run_happy_path`), executed at the TCL-005 tip `9785428` on SQLite.
- The Godot behaviour comes from reading NOTES-TCL-014a and NOTES-TCL-014b and the test names. QA last ran the Godot tests at an earlier TCL-014a commit, not at `4dec5a1`.
- The UI descriptions come from the templates and URL confs; no browser was used.
- Step 2 (v2.1): the path, auth, body and responses were read in `views.py`, `urls.py` and `test_visit_transition.py` on `main` @ `83949ef`; the `curl` was not executed against a running stack.
- v2 (review fixes): the `curl` commands, the `X-OES-Token` header, the `?status=released` filter, the second-call responses, the release permission and the AC3.8 field names were checked by reading the code (re-read on `main` @ `83949ef` for v2.1). None of the commands was executed against a running stack.
- v2.3 (TCL-001c, 2 Nov 2026): docs only. The decisions were read in the team vault note `03 Decisions.md` (up to D-046) and the S2 sprint plan; no code was re-read and nothing was run. Every AC marked *not built* describes decided behaviour that no code has yet.
