# Transfer lanes and moving handoffs

## Civil infrastructure

Yard blocks, quays and rail areas share four configurable transfer sides. Each
side has its own type, edge offset, lane count, lane-center spacing and positions
per lane. Yard sides can alternatively create one position per edge bay/row.
The first lane uses the edge offset; subsequent lanes grow outward from that
side. Four lanes with two positions each create eight addressable positions.
Capacity is the total waiting capacity; `position_count` means positions per
lane, not the total number of positions.

Transfer addresses include `interface`, `lane` and `position`. Existing single
lane position IDs and codes are preserved by migration 0038. Subsequent changes
preserve positions by lane/position address, and maintain operational-position
and routing-node IDs. Removing an address used by an order or manual routing
connection is rejected.

The settings panel can be widened by dragging its divider or using the divider's
arrow keys. Controls wrap instead of overflowing the panel.

## Attached geometry

Saving a moved/rotated asset transforms attached axes, markers, transfer
geometry, yard grids, child assets, manual nodes and assigned CHE. It preserves
their routing identities. A yard block may have a rail area as its parent using
the existing asset hierarchy; no special rail-yard subclass is required.

The ASC preset uses one central logical crane rail, not two physical rail meshes.
Its `.RAIL` axis spans the block and enabled start/end transfer lanes and is
resized when the block configuration changes. Other manually drawn axes follow
the rigid asset transform but retain their authored shape.

## Equipment-relative contact positions

In Routing, select a transfer interface and set Position reference to Equipment
relative. Select its reference CHE, optionally its logical crane/quay/rail axis,
and forward/left offsets in meters. The fixed lane geometry remains unchanged.

With an axis, the CHE GPS point is projected onto the axis; longitudinal offsets
are added to its meter chainage, clamped to the axis ends. Without an axis,
offsets follow the CHE true-north heading. Multiple positions are distributed
around that center using position-center spacing; lanes add lateral spacing.

Transfer, operational-position, route-node and route-edge API reads resolve
coordinates/distances from the latest persisted CHE data without rewriting the
layout on every movement. Missing GPS produces null coordinates. Reference CHE
must belong to the same terminal and cannot be deleted while referenced.

Future routing should use `resolved_route_node_point()` for a planning snapshot
and revalidate it before dispatch. Connections remain explicit graph edges;
there is no new automatic network joining or traffic-aware routing in this change.
The editor refreshes these contacts when layout data is reloaded, not by live
MQTT streaming. See TIC40_ALIGNMENT.md for standards scope and remaining work.
