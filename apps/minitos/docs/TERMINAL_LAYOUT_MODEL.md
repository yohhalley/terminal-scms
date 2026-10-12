# Terminal layout model

The terminal editor uses composition instead of one Django table per visual
asset subtype:

1. `Location` owns versioned layouts through `LayoutVersion`.
2. `LayoutAsset` stores the shared identity, hierarchy, WGS-84 geometry,
   heading, visibility, and lifecycle for every civil-infrastructure object.
3. `StorageGrid` adds yard-specific bay, row, tier, and slot behavior.
4. `LinearAxis` adds ordered movement geometry such as gate lanes, rail tracks,
   roads, crane rails, transfer lanes, and quay faces.
5. `LinearMarker` adds positions along an axis, including bollards and travel
   limits.
6. `BerthSegment` adds quay-specific water-side and bollard-direction rules.
7. `TransferInterface`, `RouteNode`, and `RouteEdge` turn the physical layout
   into a routing graph.

Yard transfer-side choices are stored in `LayoutAsset.metadata.editor_transfers`.
The editor materializes each enabled side as a `TransferInterface`, records the
served bay/row range on that interface, and generates its `InterfacePosition`
and `OperationalPosition` records. The metadata remains a drawing recipe; the
generated interface records are the routing-domain source of truth.

Gate and rail areas therefore remain `LayoutAsset` records. Their editor
settings live in `metadata.editor_generator`, while their operational lanes or
tracks are materialized as generated `LinearAxis` records. This avoids
duplicating geometry, parent-child, versioning, and lifecycle behavior in
`GateAsset` and `RailAsset` inheritance tables. It also lets routing use the
same axis contract for roads, gates, rails, quays, and crane travel.

Quays retain the specialized `BerthSegment` model because vessel fitting,
water side, bollard numbering, and berth-plan references are durable business
concepts rather than drawing preferences.

## Visualization

The generated [model relationship diagram](MODEL_RELATIONSHIPS.md) contains all
`minitos_core` fields and relationships as a Mermaid entity relationship
diagram. Regenerate it after changing Django models:

```shell
python manage.py generate_model_diagram --output docs/MODEL_RELATIONSHIPS.md
```
