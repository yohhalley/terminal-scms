# Godot, TOS, and MQTT integration checks

The integration contract is split at the network boundaries so it can run without
production credentials or a live broker.

Run the Godot/Sparkplug checks from `godot-open-terminal`:

```sh
/Applications/Godot_mono.app/Contents/MacOS/Godot \
  --headless --path . --script tests/unit/test_terminal_integration.gd
```

This validates:

- NDEATH last-will configuration and NBIRTH on broker connection.
- All participating CHE, including powered-off CHE, emit DBIRTH followed by DDATA.
- `working: true` DDATA is due every 2 seconds and powered-on idle DDATA every 20 seconds.
- `powered_on: true -> false` emits DDATA with `PoweredOn=false`; DDEATH remains a device-accessibility event.
- Sparkplug topic names and nonempty protobuf payloads.
- Duplicate DCMD delivery creates one action and replays the same result identity.
- A same-session restart restores the configuration revision, in-flight command, and latest persisted CHE position.
- Screen, world, latitude, and longitude coordinate conversion.

Run the TOS API contract from `minitos`:

```sh
.venv/bin/python manage.py test minitos_core.test_godot_che_integration --settings=minitos_site.settings_test --noinput
```

This validates that Godot CHE snapshots survive the REST API round trip and that
the TOS rejects contradictory power, activity, availability, and maintenance facts.

## Headless simulation checks (TCL-014a, TCL-014b)

```sh
godot --headless --path . --script tests/unit/test_headless_sim.gd
```

This covers:
- seed determinism and result ids
- the fixed-step sim clock
- the QC discharge state machine, with the container on the QC at command
  receipt, a seeded hold, and an early or late TT
- HandoffId pairing rules
- the cancel DCMD, including the next command being accepted after a cancel
- the D-013 TT no-show time-out followed by an OES cancel
- `Command/Type` routing
- configurable hold waits
- ordered outbox replay after a restart
- device id mapping
- MQTT packet-id wrap
- sim-time command expiry and `Sim/TimeMs` + `Session/Id` on periodic DDATA
- TCL-014b, TT to RTG at `BLOCK01-TL`:
  - the RTG stack state machine (pick, seeded hold, drop into a dummy slot, late
    TT, no-show, cancel)
  - the full QC to TT to RTG chain with a fixed seed, asserting the ordered
    device/event/`Work/State` sequence (D-016)
  - a late TT at the RTG, RTG cancel while waiting and while holding
  - determinism across two runs, and a wrong HandoffId that never pairs
  - `assign_handoff` rules (no H2 before H1 completes, one active hand-off per
    TT), RTG receiver/visit/D-011 block checks, and the configurable RTG hold

Environment: `SIMULATOR_SEED`, `SIMULATOR_RUN_ID`, `SIMULATOR_FIXED_STEP_MS`,
`SIMULATOR_TIME_SCALE`, `SIMULATOR_START_EPOCH_MS`,
`SIMULATOR_DEVICE_ID_MODE` (`name`, the default, uses MiniTOS `Che.name` such as
`QC01`; `legacy` uses `CHE_{id}`), and
`SIMULATOR_QC_WAIT_MIN_S` / `SIMULATOR_QC_WAIT_MAX_S`. `SIMULATOR_QC_HOLD_WAIT_MIN_S`
/ `_MAX_S` override the general wait range. `SIMULATOR_TT_NO_SHOW_TIMEOUT_S`
sets the no-show time-out (default 300; the RTG uses the same value).
`SIMULATOR_RTG_WAIT_MIN_S` / `SIMULATOR_RTG_WAIT_MAX_S` (default 10..30) and
`SIMULATOR_RTG_HOLD_WAIT_MIN_S` / `_MAX_S` set the RTG hold. Each variable also
accepts a `SIM_*` alias. See `NOTES-TCL-014a.md` and `NOTES-TCL-014b.md` for open
points.

## Rail movement checks

```sh
/Applications/Godot_mono.app/Contents/MacOS/Godot \
  --headless --path . --script tests/unit/test_che_rails.gd
```

This exercises fixed alignment, STS perpendicular/water-facing orientation,
forward/reverse gantry travel, scope limits,
polyline corners, physical endpoint-only RTG transitions, rail speed limits,
cancellation/arrival, and rotated/scaled map-frame conversion. It uses real
CHE actions and a synthetic world without a broker or TOS credentials.

The corresponding Django contract suite is `minitos_core.test_che_rails`.
RTG free-driving stages currently use direct segments, not road-graph routing.

## Live local MQTT broker

The unit checks use fakes. This live check creates an independent MQTT observer,
subscribes it through the broker, runs the Godot node lifecycle, and verifies all
messages delivered by a broker on `127.0.0.1:1883`:

```sh
/Applications/Godot_mono.app/Contents/MacOS/Godot \
  --headless --path . --script tests/integration/test_local_mqtt_broker.gd
```

The check requires NBIRTH, DBIRTH, powered-on and powered-off DDATA, explicit
DDEATH, and NDEATH. Its explicit DDEATH simulates lost device accessibility; it
is not the power-off transition. Override the target with `MQTT_TEST_HOST` and
`MQTT_TEST_PORT` when needed.
