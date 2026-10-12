# Godot Open Terminal

A Godot-based terminal sandbox for connecting to a Minitos server and an MQTT broker.

## Current Entry Point

The project starts at:

`res://source/terminal-sandbox/TerminalSandbox.tscn`

The sandbox flow is intentionally smaller than the previous RTS-style game:

1. Choose the Minitos server URL and port. The client normalizes it to `/minitos_core`.
2. Check that the server is reachable.
3. Enter Minitos credentials and verify them against `/minitos_core/locations/`.
4. Enter an MQTT broker host and port and confirm the connection through the Sparkplug B manager.
5. Load terminals from `/minitos_core/locations/` after both connections succeed.
6. Choose the terminal to work with.
7. The workspace loads the selected terminal map dimensions and areas from `/minitos_core/locations/{location_id}/areas/`.
8. Use the workspace in Design or Operations mode.

## Design Mode

Design mode currently includes a simple grid canvas scaled to the selected Location `map_width_m` and `map_height_m`. Draw a rectangle, choose an object type, and save it to Minitos:

- Terminal areas: `POST /minitos_core/locations/{location_id}/areas/`
- Area coordinates are stored as local meter polygons: `[[x, y], ...]`

This creates the foundation for a terminal map editor while keeping the older RTS code out of the startup path.

## Minitos API Assumptions

The current client targets the Django REST endpoints in `yohhalley/minitos` branch `test`:

- `GET /minitos_core/locations/`
- `GET /minitos_core/locations/{location_id}/areas/`
- `POST /minitos_core/locations/{location_id}/blocks/`
- `POST /minitos_core/points-of-work/`

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

On connect, the manager publishes `NBIRTH`. It also sets an `NDEATH` last will before connecting and publishes `NDEATH` during explicit disconnects. Topics use the Sparkplug B form `spBv1.0/{group_id}/{message_type}/{edge_node_id}` and `spBv1.0/{group_id}/{message_type}/{edge_node_id}/{device_id}`.

Default broker fields:

- Host: `127.0.0.1`
- Port: `1883`

## Next Implementation Steps

- Load existing blocks, CHE, and points of work for the selected terminal into the canvas.
- Add dedicated REST payloads once roads, buildings, parking areas, and CHE have first-class Minitos endpoints.
- Expand Operations mode with truck, vessel, and train arrival simulation.
- Move the old RTS scenes into an archive folder or delete them once the sandbox replaces their useful pieces.
