# Godot Open Terminal

A Godot-based terminal sandbox for connecting to a Minitos server and an MQTT broker.

## Entry points

The desktop editor starts at:

`res://source/terminal-sandbox/TerminalSandbox.tscn`

The Docker/Linux simulator starts at:

`res://source/headless/HeadlessSimulator.tscn`

The headless scene has no HUD, viewport, camera, or rendered terminal assets. It
loads terminal/CHE configuration from MiniTOS, subscribes to OES commands,
simulates geographic movement, and publishes Sparkplug telemetry plus JSON
command results.

The sandbox flow is intentionally smaller than the previous RTS-style game:

1. Choose the Minitos server URL and port. The client normalizes it to `/minitos_core`.
2. Check that the server is reachable.
3. Enter Minitos credentials and verify them against `/minitos_core/locations/`.
4. Enter an MQTT broker host and port and confirm the connection through the Sparkplug B manager.
5. Load terminals from `/minitos_core/locations/` after both connections succeed.
6. Choose the terminal to work with.
7. The workspace loads the selected terminal map dimensions and areas from `/minitos_core/locations/{location_id}/areas/`.
8. Use the workspace in Design or Operations mode.

## Minitos API Assumptions

The current client targets the Django REST endpoints in `yohhalley/minitos` branch `test`:

- `GET /minitos_core/locations/`
- `GET /minitos_core/locations/{location_id}/layout-assets/`
- `GET /minitos_core/locations/{location_id}/linear-axes/`
- `GET /minitos_core/locations/{location_id}/ches/`

Authentication uses HTTP Basic Auth, which is supported by Django REST Framework defaults.

## Rail-constrained CHE

The versioned-layout CHE API supplies a read-only `rail_motion` descriptor from
the CHE's assigned asset and operating scopes. Fixed cranes follow the logical
rail centerline and retain its alignment in either travel direction. RTGs leave
and enter through physical rail endpoints before moving freely between blocks.
Use Refresh Terminal Layout after editing assignments or rails. The free stage
is currently direct driving, not road-graph or traffic-aware routing.

See `tests/integration/README.md` for the headless rail movement checks.

## MQTT Assumptions

The broker check uses `res://source/terminal-sandbox/MQTTNodeClient.gd` and `res://source/sparkplugB/SparkPlugMQTTManager.gd`, backed directly by `res://addons/mqtt/mqtt.gd`.

- `mqtt.gd` owns only MQTT 3.1.1 transport, sockets, subscriptions, and acknowledgements.
- `SparkPlugMQTTManager.gd` owns the edge-node session, topics, protobuf payloads, sequence numbers, NBIRTH, and NDEATH.
- `MQTTNodeClient.gd` owns CHE registration and the DBIRTH/DDATA/DDEATH lifecycle.
- Versioned JSON topics carry OES commands, normalized equipment state, and command results.

On connect, the manager publishes `NBIRTH`. It also sets an `NDEATH` last will before connecting and publishes `NDEATH` during explicit disconnects. Topics use the Sparkplug B form `spBv1.0/{group_id}/{message_type}/{edge_node_id}` and `spBv1.0/{group_id}/{message_type}/{edge_node_id}/{device_id}`.

Default broker fields:

- Host: `127.0.0.1`
- Port: `1883`

## Headless environment

The Linux runtime reads `MINITOS_URL`, `MINITOS_USERNAME`,
`MINITOS_PASSWORD`, `SIMULATOR_LOCATION_ID`, `SIMULATOR_TERMINAL_CODE`,
`MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `UNS_ROOT`,
`SPARKPLUG_GROUP_ID`, and `SPARKPLUG_NODE_ID`.

Run it locally without Docker:

```bash
godot --headless --path . res://source/headless/HeadlessSimulator.tscn
```

The headless simulator publishes working CHE every two seconds and idle CHE
every twenty seconds. Commands complete within a 0.5-meter target radius.

The headless simulator intentionally uses a simple straight-line motion model.
Road-graph path execution, crane-specific actions, collision handling and
acknowledged manual state commands remain later execution features.
