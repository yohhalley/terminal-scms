# TIC4.0 alignment record

Last reviewed: 2026-09-24

Official references reviewed:

- [TIC4.0 2026.020 release](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/3003842571/2026.020+Release)
- [TIC4.0 ontology](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/2493579276/TIC4.0+Ontology)
- [Terminal](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/2092171265/Terminal)
- [Place whitepaper](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/2188345345)
- [Crane Tracks](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/2117140481)
- [Coordinate](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/48070837/Coordinate)
- [Logical](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/48070844/Logical)
- [PAS 4000 versus legacy TIC4.0 semantics](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/2342584321/2026.018+PAS+4000+vs+TIC+4.0+legacy+semantic)
- [CHE](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/998309889/CHE)
- [Energy Supply](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/2265710593/Energy+Supply)
- [Energy Storage System](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/2233991192/Energy+Storage+System)
- [Idle](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/37650433/Idle)
- [Off 2023.009](https://tic40.atlassian.net/wiki/spaces/TIC40Definitions/pages/1105526785/Off+2023.009)

## CHE subject hierarchy

MiniTOS represents the complete CHE and its TIC4.0 subjects using
`ComponentType`, `EquipmentTypeComponent`, and `CheComponent`.

| MiniTOS component code | TIC4.0 subject | Notes |
| --- | --- | --- |
| `che` | `che` | Root equipment subject. |
| `drive` | `drive` | Horizontal travel. |
| `hoist` | `hoist` | Vertical load movement. |
| `trolley` | `trolley` | Trolley movement on a crane. |
| `gantry` | `gantry` | Gantry movement. |
| `boom` | `boom` | Boom/luffing movement. |
| `spreader` | `spreader` | Load interface and twistlock state. |
| `energysupply` | `energysupply` | Energy conversion/delivery system; an `Engine` is one implementation. |
| `energystoragesystem` | `energystoragesystem` | Battery pack or fuel storage system. |

The Python constants `POWER_SOURCE` and `ENERGY_STORE` remain aliases for
developer familiarity, but their persisted component codes use the current
TIC4.0 terminology.

## Terminal layout and Place mapping

MiniTOS models physical and logical terminal topology independently from CHE
telemetry:

| MiniTOS concept | TIC4.0 relationship | Notes |
| --- | --- | --- |
| `Location` | Terminal / Place context | Terminal identity and geographic frame. |
| `LayoutAsset` | Physical Place representation | Land, water, quay, yard, block, parking, road corridor, rail area, gate, or building geometry. |
| `OperationalPosition` | Addressable Place | Yard slots, transfer positions, work points, parking positions, and carrier slots. |
| `latitude` / `longitude` and GeoJSON | Coordinate | WGS-84 longitude/latitude is authoritative; local simulator coordinates are derived. |
| `logical_address` | Logical | Structured terminal address such as block/bay/row/tier or interface/position. |
| `PointOfWork` | Operational dispatch reference | May link to an operational position and routing node. |

`LayoutVersion`, `LinearAxis`, `TransferInterface`, `AccessPolicy`,
`RouteNode`, `RouteEdge`, `CheOperatingScope`, and `LayoutPreset` are MiniTOS
extensions. They encode executable layout constraints and routing behavior; they
must not be presented as TIC4.0-defined subjects.

The current geometry payload is a deliberately small GeoJSON subset (`Point`,
`LineString`, and `Polygon`) in WGS-84. It does not yet implement the full OGC
JSON-FG feature model referenced by TIC4.0. Any external TIC4.0 encoder must map
these records into the publication's expected Place/Coordinate representation.
Saving a terminal-boundary asset derives `Location.map_origin_latitude` and
`map_origin_longitude` from the boundary's WGS-84 bounding-box midpoint. This is
a MiniTOS local-frame/editor convention; it does not replace the boundary
geometry or introduce a separate TIC4.0 coordinate fact.

### Transfer lane and moving handoff review

Rechecked the current public 2026.020 release, the Place whitepaper, and Logical
definition. Place distinguishes fixed and mobile places and supports nested
reference frames. The direct Coordinate page was unavailable during this review;
the Place whitepaper supplies the applicable coordinate/frame guidance.

`TransferInterface.lane_count`, meter spacing and `(lane, position)` addressing
are MiniTOS layout extensions, not new TIC4.0 subjects. Equipment-relative
positions reference a CHE and optionally a logical axis. Their forward and left
offsets follow the planar X-forward/Y-left convention, with true-north bearing;
API output resolves to WGS-84 using the latest CHE coordinates. Missing CHE GPS
must yield an unresolved position, never an invented origin. Stored geometry is
a fallback/static lane description, not authoritative live equipment telemetry.

One logical crane rail describes the travel range; it does not claim to model
the physical pair of rails. Moving a parent asset transforms its children and
attached records together. Full JSON-FG frame encoding and interoperable
equipment-relative technical-location payloads remain future encoder work.

### Rail motion review

The Crane Tracks definition describes parallel physical rails, including curved
tracks. The Place whitepaper describes rail-relative position by centerline arc
length and a local tangent frame. MiniTOS uses `LinearAxis` as that logical
centerline, with `CheOperatingScope` minimum/maximum chainage in meters. The CHE
REST projection and simulator movement follow all polyline segments and use the
local tangent to derive true-north heading. Godot's local forward axis is -Z;
this is an internal rendering convention, converted to geographic bearing at
the API boundary, not a TIC4.0 coordinate payload.

Fixed/repositionable classification and RTG endpoint-only entry/exit are MiniTOS
simulation rules, not TIC4.0-defined statuses. Scope boundaries inside a physical
rail are not exits. No power/activity semantics or database fields changed in
this update. Physical paired rails, gauge/clearance envelopes and full JSON-FG
frame encoding remain outside this logical-centerline MVP.

STS orientation is derived from the local crane-rail tangent and the active
`BerthSegment.water_side`. Its travel axis remains the rail, while its equipment
heading is perpendicular and water-facing. This is a MiniTOS rendering and
simulation rule; it does not introduce a new TIC4.0 state or subject.

### Aggregate routing review

The 2026.020 release and Place whitepaper were rechecked for this routing update.
TIC4.0 describes terminals and physical places, geographic/technical/logical
location, and fixed or mobile subjects. It does not prescribe MiniTOS route-node,
route-edge, road-junction, lane-direction, access-policy, or pathfinding records.

MiniTOS therefore treats asset hubs, transfer-interface hubs, generated
position-membership links, interface-to-road links, and road-junction links as
internal executable-layout extensions. They do not replace the authoritative
WGS-84 geometry or logical address of any `OperationalPosition`. A detailed
transfer position remains the final Place/address; the aggregate interface and
asset hubs only prevent the routing graph from requiring a manually maintained
edge for every possible position pair.

Road lane count, direction, capacity, speed limit, allowed-equipment policy and
the selected adjacent road are operational constraints, not TIC4.0 telemetry or
standard-defined subjects. An external TIC4.0 encoder must continue to publish
the underlying terminal/place/location semantics and must not present generated
graph hubs as standardized physical assets.

### Quay traffic-lane and service-scope review

Reviewed 2026-09-28 against the current public TIC4.0 Crane Tracks definition
and the 2026.020 terminal/Place material. TIC4.0 describes crane tracks as two or
more physical parallel rails supporting STS, RMG, and ASC gantry motion, and its
examples express crane position along a named crane rail. MiniTOS continues to
represent gantry travel with `LinearAxis.axis_kind = crane_rail` (or the quay-face
fallback) and chainage-limited `CheOperatingScope` records.

Generated quay service, under-crane, and backreach lanes are road centerlines,
not crane tracks. `CheOperatingScope.service_lane_axis` is a MiniTOS operational
extension identifying the road lane served by a quay crane; it does not change
the crane's gantry axis or claim to be a TIC4.0 subject. The API requires that
this reference be a road centerline on the same scoped asset/layout. Road nodes,
spatial interface attachments, perpendicular editor constraints, and derived
edge codes remain internal executable-layout behavior.

## State mapping

| MiniTOS fact | TIC4.0 relationship | Storage |
| --- | --- | --- |
| `powered_on` | `On` / `Off` | Cached on `Che`, canonical `che.on.status` signal, and state intervals. |
| `working` | `Working` / `Idle` | Cached on `Che`, canonical signal, and state intervals. |
| `is_available` / `dispatch_available` | MiniTOS extension | Dispatch eligibility, independent from TIC4.0 activity. |
| `is_dispatched` | MiniTOS extension | Active assignment/instruction indicator. |
| `is_in_maintenance` | TIC4.0-related extension | Independent maintenance fact and interval state. |

### Order execution and cargo custody review

Reviewed 2026-10-12 against the public TIC4.0 2026.020 glossary, the 2026.019
chain-of-command publication, and the current Place and yard-inventory guidance.
MiniTOS `Order.status`, dispatch priority, release/completion timestamps, route
plans, and leg execution states are internal TOS/OES workflow facts. They must
not be published as if TIC4.0 prescribed this exact state machine. External
encoders should map the order and leg records to the applicable Job Instruction,
planned/actual location, and execution-time concepts.

`ContainerVisit.status = OnEquipment` records temporary cargo custody while a
container is no longer occupying a static yard `Position` and is carried by a
CHE. `ContainerVisit.current_che` identifies that moving subject. This follows
the TIC4.0 distinction between stationary yard Places and equipment-relative
mobile Places, but it remains a compact MiniTOS implementation rather than a
complete TIC4.0 Place payload. Pickup and drop are atomic process events: pickup
clears the stationary occupant before establishing CHE custody, and drop claims
the destination before clearing that custody. Coordinates remain WGS-84
observations and do not replace the logical position or CHE reference.

Canonical motion signals use per-axis deadbands. A CHE is evaluated as Working
when it is On and either has an active dispatch instruction or at least one
supported motion signal exceeds its deadband. It is Idle when On with neither.
Off and Maintenance remain distinct interval states.

MiniTOS rejects these contradictory REST snapshots:

- Working while powered off.
- Available while powered off.
- Working or available while in maintenance.

## Signal model

`SignalDefinition` stores the canonical name, value type, unit, TIC4.0 path,
and applicable publication. `SignalBinding` maps Sparkplug B, MQTT, REST,
simulator, or manufacturer metrics onto that definition. `CurrentSignalValue`
stores only the current typed value, timestamp, quality, and optional sequence.

High-frequency raw telemetry is deliberately not persisted in the transactional
MiniTOS database. MQTT or a time-series store should retain that history.
MiniTOS persists state intervals and handling-cycle outcomes needed for OEE.

Legacy REST fields remain compatible but are no longer CHE columns:

- `fuel_level` maps to `energystoragesystem.energy.level`.
- `twistlock_closed` maps to `spreader.locked.status`.
- CHE and engine running hours map to separate cumulative signals/counters.
- Equipment-type speed, turning, engine, power, and capacity fields map to
  component profile defaults.

## Known gaps and deliberate extensions

- `tic40_path` records semantic intent but is not yet a complete PAS 4000 message
  encoder. Sparkplug B payload translation remains a separate integration layer.
- Raw signal debounce is synchronous in this MVP. Production ingestion needs a
  worker/window for equipment-specific idle delays and stale-signal handling.
- Health, warning, fault-code, emergency-stop, control-mode, and detailed
  maintenance subjects need dedicated models before predictive maintenance is
  authoritative.
- OEE Availability and Performance can be calculated from state intervals and
  handling cycles. Quality currently means successful versus failed/aborted
  handling cycles; this is a MiniTOS operational interpretation.
