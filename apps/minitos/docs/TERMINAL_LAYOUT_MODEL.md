# Terminal layout model

The terminal editor uses composition instead of one Django table per visual
asset subtype:

1. `Enterprise` owns stable execution `Site` identities.
2. Each terminal or inland-depot `Site` references one geographic `Location`.
3. `Location` owns versioned layouts through `LayoutVersion`.
4. `LayoutAsset` stores the shared identity, hierarchy, WGS-84 geometry,
   heading, visibility, and lifecycle for every civil-infrastructure object.
5. `StorageGrid` adds yard-specific bay, row, tier, and slot behavior.
6. `LinearAxis` adds ordered movement geometry such as gate lanes, rail tracks,
   roads, crane rails, transfer lanes, and quay faces.
7. `LinearMarker` adds positions along an axis, including bollards and travel
   limits.
8. `BerthSegment` adds quay-specific water-side and bollard-direction rules.
9. `TransferInterface`, `RouteNode`, and `RouteEdge` turn the physical layout
   into a routing graph.

Publishing a valid layout freezes the complete layout, route graph, and CHE
master definition in a checksummed `SiteConfigurationSnapshot`. OES consumes
that immutable revision; it does not query MiniTOS layout tables or duplicate
their schema.

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
