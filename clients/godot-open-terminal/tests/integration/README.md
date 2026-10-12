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
- `powered_on: false -> true` emits DBIRTH followed immediately by DDATA.
- `working: true` DDATA is due every 2 seconds and powered-on idle DDATA every 20 seconds.
- `powered_on: true -> false` emits DDEATH.
- Sparkplug topic names and nonempty protobuf payloads.
- Screen, world, latitude, and longitude coordinate conversion.

Run the TOS API contract from `minitos`:

```sh
.venv/bin/python manage.py test minitos_core.test_godot_che_integration --settings=minitos_site.settings_test --noinput
```

This validates that Godot CHE snapshots survive the REST API round trip and that
the TOS rejects contradictory power, activity, availability, and maintenance facts.

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

The check requires NBIRTH, DBIRTH, DDATA, DDEATH, and NDEATH. Override the target
with `MQTT_TEST_HOST` and `MQTT_TEST_PORT` when needed.
