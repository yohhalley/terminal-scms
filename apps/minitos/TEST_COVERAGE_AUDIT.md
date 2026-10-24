# Django test coverage audit

Audit date: 2026-10-19

The discovered Django suite contains 128 tests, plus 51 focused terminal-editor
interaction tests executed with Node's built-in test runner:

- `minitos_core/tests.py`: 35
- `minitos_core/test_yard_grid.py`: 13
- `minitos_core/test_berth_planning.py`: 7
- `minitos_core/test_model_api_regressions.py`: 10
- `minitos_core/test_che_components.py`: 9
- `minitos_core/test_che_component_migration.py`: 1
- `minitos_core/test_layout_migration.py`: 1
- `minitos_core/test_godot_che_integration.py`: 1
- `minitos_core/test_terminal_browser.py`: 4
- `minitos_core/test_model_diagram.py`: 2
- `minitos_core/test_transfer_lanes.py`: 16
- `minitos_core/test_che_rails.py`: 15
- `minitos_core/test_order_routing.py`: 1
- `minitos_core/test_order_execution.py`: 8
- `minitos_core/test_sites.py`: 5

The JavaScript interaction suite is
`minitos_core/static/terminal_editor_map_tools.test.js`. It covers unrestricted
vertex movement, first-click drawing feedback, clean double-click completion,
quay bollard/water-side guides, gate lane and rail track generation, yard-grid
labels, transfer-interface geometry/ranges, map cursor modes, and basemap layer
restoration. It also checks individual ground-cell geometry/centers, slot skew,
bay-axis orientation, resizing, and selection-independent saved context.
CHE geometry tests check centered, meter-sized footprints, true-north heading
arrows, nested/type dimension fallback, and invalid geographic coordinates.
Rail geometry tests cover inherited assignments, true-north tangent alignment,
scope clipping, fixed-crane projection, STS water-side orientation (including a
reversed rail), and freely positioned transport equipment.
Road-network tests cover magnetic snapping to enabled road vertices, snap
tolerance/exclusion, aggregate routing-endpoint filtering, and hiding generated
per-position nodes from the manual connection workflow. They now also cover
transfer-interface endpoint/position snapping, segment-projected road-node
insertion, Shift-constrained perpendicular branches, and generated quay service,
under-crane, and backreach lane centerlines. Backend graph tests also cover
automatic, stable intersections where road centerlines cross between vertices.

`minitos_core/static/terminal_editor_render.test.js` executes the real editor
script with DOM/MapLibre adapters. It checks saved footprint/grid/transfer source
population, layer order, explicit provider-supported label fonts, selecting an
asset, starting/clearing drafts, and all three basemap switches.
It now checks parallel transfer lanes on yard/quay/rail drafts and pointer/keyboard
panel resizing. Geometry tests cover outward lane spacing, quay water-side frames,
and logical crane rails extending over multi-lane transfers.
Equipment workflow tests cover rectangle/arrow selection, live rotation/type/
power/GPS previews, new equipment placement, dragging equipment without changing
saved records, preserving equipment geometry across all three basemap styles, and
limiting assigned-area choices to quay, yard-block, parking, and rail assets.
The rail workflow checks locked fixed-crane headings, projected dragging, and
unlocking the heading after switching to a freely driving equipment type.
The CHE API regression suite rejects placement on ineligible layout assets while
retaining the existing same-terminal ownership validation.
The map-first workflow tests named asset tools, press-drag-release configured
placement, automatic object codes, contextual bottom-panel selection, internal
copy/paste duplication, segment-level road snapping, and right-click road save.
They also verify 0.4 m bay/row defaults for automated and RTG yard blocks.
Saved transfer-point labels remain hidden until their owning asset is selected,
while the transfer points themselves remain visible and selectable.
It also reproduces the configured-asset drag followed by immediate road creation,
covers graceful operation with a stale map-tools helper, and validates magnetic
multi-node road drawing, repeated Backspace removal, right-click persistence, and
selecting/deleting an existing road through its owning infrastructure asset.
Validation coverage now identifies disconnected graph groups by asset and node,
while road graph coverage verifies that moving a shared vertex carries every
snapped road endpoint and preserves the compiled junction.

Rail backend tests cover create/update alignment, min/max chainage, physical
endpoint availability, individual/type scopes, RTG reposition permission, quay
face fallback, axis priority, inherited assets, curved rails, foreign terminals,
disabled axes, and read-only serialization without rewriting stored poses.
They also cover creating and updating rail-bound CHE by meter chainage, persisting
the gantry travel envelope, exposing the assignment through the CHE API, allowing
an STS assigned to a quay to use a child crane rail, and rejecting a pose outside
the envelope or beyond the physical rail length.
`godot-open-terminal/tests/unit/test_che_rails.gd` additionally checks rail-only
travel, reverse gantry motion, polyline corners, RTG rail/free/rail transitions,
speed limits, cancellation, arrival, and rotated/scaled geographic map frames.

Transfer backend tests cover lane/position addressing, capacity and geometry,
identifier preservation during resizing, protected routing connections, moving
asset hierarchies, attached equipment, preset crane-rail resizing, CHE-relative
coordinate resolution, validation, and migration of existing position addresses.
They now also cover aggregate asset/interface hubs, a constant-size interface to
road attachment for many detailed positions, access-policy propagation, API
serialization, road-axis validation, bidirectional road junction compilation,
graph reachability, generated-junction identifier preservation on resync, and an
end-to-end two-block route through aggregate interfaces and a road with no manual
per-position edges. Crossing-road coverage also verifies that generated
intersection nodes and access edges are removed when one of their roads is
deleted.
The data migration test confirms existing transfer positions gain aggregate hubs
and generated membership links without replacing their route-node identifiers.

For a read-only browser smoke test with the real MapLibre renderer, generate a
fixture with `tools/render_terminal_editor_fixture.py`. Its API responses are
synthetic and writes are rejected, so it does not touch the terminal database.
For example, run `.venv/bin/python tools/render_terminal_editor_fixture.py
/tmp/editor-visibility-fixture.html` with the local server already running, then
open that HTML file. `--base-url` selects a different running server for scripts.
Browser verification on this update confirmed all 160 ground cells, 58 transfer
points, civil footprints and the perimeter remain visible on Streets, Satellite,
and Dark while starting another asset. The existing Test Terminal's saved civil
footprints and perimeter were also verified without modifying its data.
The new fixture includes four yard transfer lanes and two quay/rail transfer
lanes. Panel controls were also checked at desktop and narrow viewport widths.
The fixture now includes TT, SC, RTG, and QC equipment with distinct dimensions
and headings. Browser verification confirmed four equipment footprints alongside
the saved layout on Streets, Dark, and Satellite, direct footprint selection,
and live heading rotation. A 390px viewport preserved all equipment source
records without horizontal page overflow. No application data was changed.
This rail update also visually checked RTG and QC assignment alignment and locked
headings with real MapLibre rendering. Input/assignment event automation was
blocked by browser instrumentation errors; those workflows pass the DOM-adapter
tests but still need a native-browser smoke check.

`test_pow_models.py` and `test_visit_cargo_routes.py` are demonstration scripts,
not Django `TestCase` classes, so `manage.py test` does not discover them.

## Model coverage

Substantive automated coverage exists for:

- ISO/container inventory: `ISOType`, `ContainerOwner`, `ContainerUnit`,
  `ContainerVisit`, `Pile`, and `Position`.
- Versioned layout: `LayoutVersion`, `LayoutAsset`, `StorageGrid`,
  `LinearAxis`, `LinearMarker`, `BerthSegment`, `AccessPolicy`,
  `TransferInterface`, `InterfacePosition`, and `OperationalPosition`, including
  terminal-boundary synchronization of the location map origin.
- Routing topology: `RouteNode` and `RouteEdge`, including axis graph
  compilation, directionality, movement mode, manual handoff edges, graph
  boundary validation, and interface-node materialization.
- Yard generation: pile/slot creation, geographic placement, naming,
  rotation/resizing, identifier preservation, protected-position shrink
  rejection, and yard-slot operational-position synchronization.
- Berth planning: water side, forward/reverse bollard numbering, custom
  spacing, vessel-length fitting, productivity duration/ETD, weekly visit
  generation, and supporting-equipment requirements.
- Equipment: `Che`, `EquipmentType`, component profiles, installed components,
  signals, state intervals, handling cycles, TIC4.0 state validation,
  terminal-scoped layout-asset assignment, and `CheOperatingScope` rail
  constraints. Service-lane scope validation covers same-asset road axes and
  rejects a lane from another asset.
- Work execution: nested `Order` creation, directed shortest-path planning,
  gate-to-transfer-to-yard leg decomposition, `OrderRoutePlan`, `OrderLeg`, and
  `OrderLegRouteEdge` persistence and reads. Execution coverage includes planned
  order release, MES-token authorization, dispatch/start/complete callbacks,
  CHE state projection, idempotent pickup, atomic occupied-slot rejection,
  equipment custody, yard drop, and direct status-update protection.
- Business processes: hierarchy, versioning, Node-RED flow validation, and
  execution records.
- Released-order outbox payloads include operational and inventory positions,
  and duplicate OES execution events are ignored by the MQTT integration inbox.
- Migrations: CHE component data migration and the legacy-layout retirement to
  versioned assets/routing, including preservation of terminals and CHE.
- Enterprise/Site identity: terminal-editor creation, default Enterprise,
  terminal-only validation, immutable Site codes, transactional Site-created
  outbox events, checksummed configuration snapshots, revision history, and
  authenticated OES snapshot retrieval.
- Multi-Site release contracts: v2 Enterprise/Site topic identity is the
  default; opt-in legacy dual publication reuses the same event ID. The removed
  MiniTOS simulator account tests reflect that headless Godot now bootstraps only
  from OES with a session-scoped token.

Models without substantive automated behavior coverage:

- `POD`
- `PileBooking`
- `RouteEdgeRequirement`
- `LayoutPreset` CRUD (preset seeding is covered)

## API and view coverage

Covered endpoint families include containers, locations, storage grids and
nested piles/positions, layout assets, access policies, linear axes, berth
segments, transfer interfaces, route nodes/edges, CHE and component telemetry,
carrier services/visits, business processes, terminal browser/layout/CHE editor
pages, Enterprise/Site identity and snapshot retrieval, and nested orders/order
legs.

Not yet directly covered:

- Layout-version clone/archive workflows and update/delete operations.
- Route-edge component requirements.
- Operational-position direct writes and delete protection.
- Non-weekly carrier recurrence patterns.
- Nested visit cargo routes and pile bookings.
- Bulk visit creation and visit status-transition endpoints.
- User/group endpoints and several legacy generic views.
- Full automated browser-driven MapLibre event integration. Editor workflow,
  geometry rules and backend contracts are covered; renderer smoke checks are
  currently manual, and native marker dragging still needs cross-browser checks.

## Residual risks

- `transition_visit_status` and `transition_container_status` still reference
  legacy state-manager symbols and require a separate repair.
- Container and storage-grid list querysets emit unordered-pagination warnings.
- Road intersection discovery compiles non-collinear geometric centerline
  crossings into generated route nodes and edges. It does not yet model
  collinear overlaps, grade separation, turn lanes, or right-of-way.
- RTG free-driving stages use a direct segment between physical rail endpoints.
  They do not yet use the road graph, avoid obstacles/intervening assets, or model
  traffic. Rail travel itself follows the full scoped logical centerline.
- Equipment-relative handoffs resolve from the latest persisted CHE GPS data at
  API read time. A future routing engine must consume resolved snapshots, not the
  stored fallback coordinates, and revalidate before execution. MQTT now carries
  order lifecycle events, but traffic-aware pathfinding and automatic joining
  remain future work.
