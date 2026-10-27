# Demo scenarios: seed, reset and steps (TCL-006)

`import_v1` is the Sprint 1 demo world: one vessel discharge into one yard block.
A reset puts both service databases back into exactly the same state, so a demo
can be rerun with the same IDs and the same order of events (D-016: identical
states and order, not byte-identical messages).

## Commands

| What | Make | Without Make |
| --- | --- | --- |
| Reset and seed | `make scenario-reset` (`SCENARIO=import_v1`, `SEED=n`, `CONTAINERS=1..5`) | see the four commands below |
| Release the discharge order | `make scenario-step STEP=release-discharge [ORDER=id]` | `docker compose exec -T minitos python manage.py scenario_step import_v1 release-discharge` |
| Dispatch the next step | `make scenario-step STEP=dispatch-next [ORDER=tos-id]` | `docker compose exec -T terminal-oes python manage.py scenario_step import_v1 dispatch-next` |
| Compare two resets | `make scenario-dump` | `... manage.py scenario_dump --checksum` in each service |

`make scenario-reset` runs, in this order:

1. `terminal-oes: scenario_reset import_v1 --noinput`: empties every `execution`
   table. It refuses while a simulation session is active; stop the simulation
   on the OES Simulations page first (or pass `--force` by hand).
2. `minitos: clear_retained_mqtt`: publishes an empty retained payload to every
   retained topic under `scms/#` and `spBv1.0/#`. The OES Sparkplug host
   `spBv1.0/STATE/<host>` topic is never cleared. `--dry-run` only lists topics.
3. `minitos: scenario_reset import_v1 --noinput`: empties every `minitos_core`
   table, restarts id sequences, restores the CHE component and signal
   catalogues, seeds the scenario and publishes the Site through the normal
   layout publish path (outbox `scms.site.created.v1` and
   `scms.site.configuration.published.v1`). It prints `canonical-sha256 ...`.
4. `terminal-oes: scenario_wait_ready`: waits up to 60 s for the OES Site
   projection to become READY (needs the broker, `minitos-outbox` and
   `oes-uns-consumer` to be running).

Each service resets only its own database through its own management command;
nothing writes across databases. Django users (admin accounts) are kept.

## What `import_v1` seeds

- Location "Test Terminal", Enterprise `terminal-velocity`, Site `test-terminal`.
- Layout v1 (published): TERMINAL, LAND, WATER; road `ROAD-01`; quay `QUAY-01`
  with `BERTH-01` and 13 bollards; yard block `BLOCK01` (10 bays x 4 rows x 3 tiers,
  the D-017 exception block); truck hand-off lanes `QC01.LANE` (under the crane)
  and `BLOCK01.TL`, both joined to the road.
- CHEs `QC01` (STS), `TT01`, `TT02` (TT), `RTG01` (RTG); work queue `QC01-WQ`.
- Vessel visit `V001` (Arrived), and 1 (up to 5) pre-advised import container(s)
  owned by `TVLU` with a valid ISO 6346 check digit, in vessel slot `0340682`
  (then `0340684`, ...).
- One PLANNED discharge order per container with an active route plan:
  QC handle (vessel slot -> QC01 lane, hand-off), TT move (QC01 lane -> BLOCK01.TL,
  hand-off, `movement_mode` road), RTG handle (BLOCK01.TL -> yard slot).

Determinism: auto ids restart at 1, Enterprise/Site ids are uuid5 under the
fixed namespace `7c0e5b1a-6d1e-4c55-9a2b-0000000000c6`, and container serials,
weights and yard slots come from `random.Random(SEED)` (default `20261026`).
Event ids, wall-clock timestamps and the snapshot checksum (it embeds
`generated_at`) still differ per run; the canonical dump masks exactly those.
Outbox delivery bookkeeping (`attempts`, `last_error`, `published_at`) is also
masked, so the checksum is the same whether or not the outbox publisher has
already drained the queue.

## Event fixture for terminal-oes

`apps/terminal-oes/execution/fixtures/scenario_import_v1_events.json` holds the
events a fresh seed publishes, so OES tests replay the real payloads. A MiniTOS
test fails when the seed changes; regenerate with

```bash
cd apps/minitos
python manage.py scenario_export_events --settings=minitos_site.settings_test \
  > ../terminal-oes/execution/fixtures/scenario_import_v1_events.json
```
