# Demo scenarios: seed, reset and steps (TCL-006)

`import_v1` is the Sprint 1 demo world: one vessel discharge into one yard block.
A reset puts both service databases back into exactly the same state, so a demo
can be rerun with the same IDs and the same order of events (D-016: identical
states and order, not byte-identical messages).

## Commands

| What | Make | Without Make |
| --- | --- | --- |
| Reset and seed | `make scenario-reset` (`SCENARIO=import_v1`, `SEED=n` default 1, `CONTAINERS=1..5`, `RESET_EPOCH=` optional) | see the steps below; pass the same `--reset-epoch` to both services |
| Release the discharge order | `make scenario-step STEP=release-discharge [ORDER=id]` | `docker compose exec -T minitos python manage.py scenario_step import_v1 release-discharge` |
| Dispatch the next step | `make scenario-step STEP=dispatch-next [ORDER=tos-id]` | `docker compose exec -T terminal-oes python manage.py scenario_step import_v1 dispatch-next` |
| Compare two resets | `make scenario-dump` | `... manage.py scenario_dump --checksum` in each service |

`make scenario-reset` first generates one reset epoch for the run
(`<UTC timestamp>-<8 random hex>`, or `RESET_EPOCH=...` if given) and prints it,
then runs, in this order:

0. `SIMULATOR_SEED=$(SEED) docker compose up --detach --no-deps --wait terminal-oes`:
   recreates `terminal-oes` only if its `SIMULATOR_SEED` differs from `SEED`
   (otherwise a no-op), so the next simulator session gets the same seed.
1. `terminal-oes: scenario_reset import_v1 --noinput --seed $(SEED) --reset-epoch <epoch>`: checks that
   OES would hand the simulator `SIMULATOR_SEED=$(SEED)` (it stops before wiping
   if not), then empties every `execution` table and stores the epoch. It refuses while a simulation
   session is active; stop the simulation on the OES Simulations page first (or
   pass `--force` by hand).
2. `minitos: clear_retained_mqtt`: publishes an empty retained payload to every
   retained topic under `scms/#` and `spBv1.0/#`. The OES Sparkplug host
   `spBv1.0/STATE/<host>` topic is never cleared. `--dry-run` only lists topics.
   It waits `--settle-seconds` (default 1) for retained messages; on a slow or
   remote broker run it by hand with `--settle-seconds 3`.
3. `minitos: scenario_reset import_v1 --noinput --seed $(SEED) --containers $(CONTAINERS) --reset-epoch <epoch>`:
   empties every `minitos_core` table, restarts id sequences, stores the epoch, restores the CHE component and signal
   catalogues, seeds the scenario and publishes the Site through the normal
   layout publish path (outbox `scms.site.created.v1` and
   `scms.site.configuration.published.v1`). It prints `canonical-sha256 ...`.
4. `terminal-oes: scenario_wait_ready`: waits up to 60 s for the OES Site
   projection to become READY (needs the broker, `minitos-outbox` and
   `oes-uns-consumer` to be running). If it times out, check that
   `minitos-outbox` and `oes-uns-consumer` run, then run `make scenario-reset`
   again. (Before QA M2 a stale higher Site revision from the previous run could
   make OES skip the new revision 1; with the reset epoch it is ignored.)

## Stale messages from the previous run (QA M2)

After a reset the ids restart at 1, the Site uuid5 is the same and both inboxes
are empty, so a message from the previous run would look like a valid new one:
OES's MQTT session is persistent (`clean_session=False`, QoS 1) and keeps queued
messages, the Makefile wipes OES while MiniTOS still has its old outbox rows, and
`minitos-outbox` / `oes-outbox` may still publish rows they read before the wipe.
In the PR #24 review a previous run's `leg_dispatched` was applied by MiniTOS
(P5) and a previous run's `order.released` was taken by OES (O2).

Both services therefore store the **reset epoch** of the run (table
`ScenarioResetEpoch`, one row, rewritten inside the reset transaction) and
fence their cross-service messages with it:

- Producers add a top-level `reset_epoch` key to every envelope they create
  while an epoch is stored: MiniTOS `scms.site.created.v1`,
  `scms.site.configuration.published.v1`, `scms.order.released.v1`; OES
  `scms.execution.event.v1`.
- Consumers (`minitos_core.services.apply_execution_message`, OES
  `execution.services.messages.process_message`) compare it with their own
  epoch. A different or missing `reset_epoch` is **ignored** (strict policy):
  no inbox row is written, nothing changes, and the message is acked.
- Every ignored message is logged at **INFO** (loggers
  `minitos_core.services.reset_epoch` and `execution.services.reset_epoch`, both
  sent to the console so drops show in container and CI logs):
  `Ignored stale message type=<schema> event_id=<id> topic=<topic>: reset_epoch seen=<epoch|none> stored=<epoch> (previous scenario run; ignored_total=<n>).`
- Each service keeps an in-process counter of ignored messages per message type:
  `services.reset_epoch.ignored_count()` (total) or `ignored_count(<schema>)`;
  `reset_ignored_count()` clears it. It restarts at 0 with the process, and the
  running total is in every log line.
- With no epoch stored (normal operation, tests, or a reset run by hand without
  `--reset-epoch`) nothing is stamped and every message is accepted, exactly as
  before. `scenario_reset` prints `reset epoch none (no stale-message fence)` then.
- Sparkplug traffic between OES and Godot is not fenced: command and simulation
  session ids are uuid4, so results for the previous run's commands are already
  rejected.
- The epoch is masked in the canonical dump (two resets still give the same
  checksum).

The field is an optional, additive v1 envelope field (see
`contracts/schemas/scms.execution.event.v1.schema.json`); the version is unchanged.

Without Make, generate one epoch and pass the same value to both services:

```bash
EPOCH=$(date -u +%Y%m%dT%H%M%SZ)-manual
docker compose exec -T terminal-oes python manage.py scenario_reset import_v1 --noinput --seed 1 --reset-epoch "$EPOCH"
docker compose exec -T minitos python manage.py clear_retained_mqtt
docker compose exec -T minitos python manage.py scenario_reset import_v1 --noinput --seed 1 --reset-epoch "$EPOCH"
docker compose exec -T terminal-oes python manage.py scenario_wait_ready
```

If either service's reset fails, `make scenario-reset` stops there with a
non-zero exit and prints
`scenario-reset: <terminal-oes|minitos> reset failed; the services are not on the same run. Fix the error above and re-run scenario-reset.`
(`scenario_wait_ready` is not run). If the MiniTOS reset fails after the OES
reset (for example the TT route error below), OES already holds the new epoch
and ignores MiniTOS's old-epoch messages until the reset is run again; rerun
`make scenario-reset`.
An epoch is 1-64 characters: letters, digits, `.`, `_`, `:` or `-`; anything else
is refused before any table is wiped.

Tests: `minitos_core/test_scenario_reset_isolation.py` (P5) and
`execution/test_scenario_reset_isolation.py` (O2) replay the previous run's
message after a reset and check the INFO log and the ignored counter;
`minitos_core/test_scenario_import_v1.py` (`ScenarioResetMakeTargetTests`) runs
the real target against a stub `COMPOSE` and checks the failure exit and message;
the contract tests check the stamped envelope against the
schema and feed goldens with and without `reset_epoch` to the MiniTOS consumer.

If the TT leg cannot be routed (QC01 lane to BLOCK01.TL), the MiniTOS reset
fails with "Reset rolled back: import_v1: no road route ..." and the previous
MiniTOS state is kept.

Each service resets only its own database through its own management command;
nothing writes across databases. Django users (admin accounts) are kept.

## What `import_v1` seeds

- Location "Test Terminal", Enterprise `terminal-velocity`, Site `test-terminal`.
- Layout v1 (published): TERMINAL, LAND, WATER; road `ROAD-01`; quay `QUAY-01`
  with `BERTH-01` and 13 bollards; yard block `BLOCK01` (10 bays x 4 rows x 3 tiers,
  the D-017 exception block); truck hand-off lanes `QC01.LANE` (under the crane)
  and `BLOCK01.TL`, both joined to the road.
- CHEs `QC01` (STS), `TT01`, `TT02` (TT), `RTG01` (RTG), all powered on,
  available and not dispatched (spec K3); work queue `QC01-WQ`. OES marks them
  available after each CHE's DBIRTH.
- Vessel visit `V001` (Arrived), and 1 (up to 5) pre-advised import container(s)
  owned by `MSCU` with a valid ISO 6346 check digit, in vessel slot `0340682`
  (then `0340684`, ...). The first container is always `MSCU1234566`; any extra
  containers get seeded serials.
- One PLANNED discharge order per container with an active route plan:
  QC handle (vessel slot -> QC01 lane, hand-off), TT move (QC01 lane -> BLOCK01.TL,
  hand-off, `movement_mode` road), RTG handle (BLOCK01.TL -> yard slot).

Determinism: auto ids restart at 1, Enterprise/Site ids are uuid5 under the
fixed namespace `7c0e5b1a-6d1e-4c55-9a2b-0000000000c6`, and extra container
serials, weights and yard slots come from `random.Random(SEED)` (default `1`).

Seed and clock (spec K7): `SEED` defaults to 1, the same as `SIMULATOR_SEED`, and
`make scenario-reset SEED=n` sets both the MiniTOS scenario RNG and the simulator
seed (OES forwards `SIMULATOR_SEED` to the runner). Seeded business times (visit
ETA/ATA, order ETC) are on the simulator clock: `SIMULATOR_START_EPOCH_MS`
1792886400000, 2026-10-25 00:00 UTC, so the vessel's ATA is sim time 0. A plain
`make up` recreates `terminal-oes` with `.env`'s `SIMULATOR_SEED` (default 1);
run `make scenario-reset SEED=n` again afterwards to keep both seeds equal.
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
