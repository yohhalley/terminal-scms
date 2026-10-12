# Terminal routing graph

MiniTOS compiles detailed layout records into a smaller operational graph. Users
draw infrastructure and assign interfaces to roads; they do not connect every
yard slot or transfer position manually.

## Graph levels

1. An asset hub represents a routable area such as a yard block, quay, gate, or
   parking area.
2. An interface hub represents one transfer interface on that asset.
3. Detailed interface-position nodes remain available as final addresses, but
   generated handoff edges connect them to their interface hub.
4. An interface can reference one adjacent road centerline. MiniTOS generates a
   bidirectional connector between the interface hub and the nearest road node.
5. Road centerlines compile into directed edges according to their configured
   lane direction. Centerlines with coincident snapped vertices receive generated
   bidirectional junction edges.

A typical discharge route can therefore be expressed as:

`yard slot -> yard-block hub -> transfer-interface hub -> road network -> remote
transfer-interface hub -> remote yard-block hub`

The route planner selects an eligible CHE for each edge based on movement mode,
the edge or axis access policy, equipment capabilities, load state, and operating
scope. Detailed yard-slot and interface-position records are resolved only at the
first and final handling stages.

## Editor behavior

- Road centerlines store lane count, direction, capacity, width, speed limit, and
  an access policy containing allowed CHE types.
- While drawing a road, a point within eight meters of an existing enabled road
  vertex, road segment, or transfer-interface position/end point snaps to it.
  Saving the road compiles road intersections and assigns every snapped interface
  to that road.
- Double-clicking an existing road projects a new draggable vertex onto its
  nearest segment. A new road started at that vertex becomes a branch; holding
  Shift while placing the branch's second point constrains it perpendicular to
  the source segment.
- Geometric centerline crossings compile stable virtual intersection nodes and
  directional access edges automatically. Shared snapped vertices continue to
  use the simpler bidirectional road-junction links.
- Transfer interfaces expose an operational axis for crane or transfer-lane
  motion. Their adjacent road is assigned spatially by drawing into the interface
  or by selecting the interface and clicking **Connect on map** followed by a road.
- Quay definitions generate separate service, under-crane, and backreach lane
  centerlines. They are ordinary road axes, so terminal roads can snap to their
  endpoints and reuse the same routing compiler.
- A CHE operating scope may reference one generated service lane on its scoped
  quay asset. This records which lane the STS/QC currently serves without making
  the service lane part of the crane's gantry travel axis.
- Manual connection selectors show aggregate area/interface hubs, road-axis
  points, and manual nodes under an advanced override. Generated per-position
  nodes are intentionally hidden from this workflow. Manual edge codes are
  derived from their endpoint codes.

## Deliberate MVP limits

- Overlapping collinear road segments remain outside the automatic-intersection
  MVP; model these with a shared centerline or explicit shared vertices.
- Intersections do not yet model turn lanes, signals, right-of-way, or congestion.
- An interface attaches to the nearest node of its selected road. Snapping the
  road to an interface creates a coincident node and therefore a zero-length
  graph attachment at the intended access point.
- Traffic-aware cost updates and route reservations remain future routing-engine
  work. The current graph records static base cost and capacity.
