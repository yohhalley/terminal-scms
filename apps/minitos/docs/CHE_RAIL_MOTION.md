# CHE logical rail motion

This feature uses the existing `LayoutAsset`, `LinearAxis`, `Che.current_asset`
and `CheOperatingScope` models. It requires no database migration.

## Assignment

1. Create a quay, yard block or rail area with an enabled `crane_rail` axis.
   Preset-generated logical rails are supported, including their transfer-zone
   extensions. The rail centerline, not the asset's rectangular boundary, defines
   travel direction and range.
2. Assign the CHE's `current_asset` to that asset or one of its children.
3. Optionally define enabled equipment or equipment-type scopes with an asset
   or axis, `min_chainage_m`, `max_chainage_m`, and `can_reposition`.
   Individual enabled scopes override equipment-type scopes.
4. Reload the editor or use Godot's Refresh Terminal Layout button.

STS/QC, RMG/ARMG/ASC/RGC and ARTG are fixed rail equipment in this MVP. RTG is
repositionable. TT, SC, AGV and other equipment retain free-driving behavior.
STS/QC can use a quay-face axis if no crane rail is available; other fixed
equipment can use a rail-track axis. Crane rails take priority. Fixed equipment
without an assigned usable axis cannot accept arbitrary driving commands.
STS/QC travel along the rail but their body heading is perpendicular to its
local tangent and points toward the active berth segment's `water_side`. Rail
point order is compared with the quay-face axis, so reversing a crane rail does
not turn an STS toward land.

## REST and editor contract

The existing nested CHE endpoints expose a read-only `rail_motion` descriptor:
mode, assigned axis ID, and applicable rails. Each rail includes its scoped
WGS-84 polyline, asset geometry, reposition permission, physical endpoint flags,
capture tolerance and speed limit. Axes from other terminals/layouts and disabled
axes are excluded.

Creation, reassignment and pose updates project fixed CHE onto their scoped rail
and set heading to its true-north tangent. RTG assignment also projects the CHE;
later free-driving pose updates are not snapped back to an old rail. Flag-only
updates and reads do not rewrite stored coordinates. The editor previews aligned
footprints and disables manual heading while attached to a rail.

## Godot actions

`TerminalWorld3D` transforms geographic rail points into the current map frame.
`TerminalChe` owns the authoritative unit transform, while `RailPath` builds
waypoints and `actions/TravelRoute.gd` executes them using the existing action
lifecycle. Selection, model, collision shape and telemetry therefore move with
the same CHE root.

- Fixed CHE project any destination onto their assigned scoped rail. Reverse
  travel preserves equipment heading rather than turning the crane around. STS
  heading remains perpendicular and water-facing while gantrying.
- An RTG targeting another block first travels to a permitted physical endpoint,
  drives freely to the destination rail endpoint, then aligns and gantries to
  the projected destination. `can_reposition=false` prevents leaving the rail.
- Min/max scope limits clip travel; an interior limit never creates a new exit.
- Rail corners are explicit waypoints. Speed honors the equipment profile and
  any lower rail speed limit. Arrival tolerance is 0.5 meters.
- The HUD/order destination uses the actual projected destination. The CHE's
  current asset follows its active rail; free RTG travel clears that assignment.
  Existing command/start, completion and cancellation REST update rules remain.

## MVP boundaries

One logical centerline represents the gantry path, not the physical rail pair.
RTG free stages are direct travel, without road-graph pathfinding, collision
avoidance, traffic, turn envelopes or avoidance of intervening block areas.
Scope speed/endpoint constraints are supported; access policies and graph edge
requirements are not yet consulted by this simulator movement planner. Targets
inside a destination asset project onto that asset's usable rail.

## Checks

From `minitos`:

```sh
.venv/bin/python manage.py test minitos_core.test_che_rails --settings=minitos_site.settings_test --noinput
node --test minitos_core/static/terminal_editor_map_tools.test.js minitos_core/static/terminal_editor_render.test.js
```

From `godot-open-terminal`:

```sh
/Applications/Godot_mono.app/Contents/MacOS/Godot --headless --path . --script tests/unit/test_che_rails.gd
```
