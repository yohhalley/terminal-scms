# Gazebo Harbour Models asset subset

This directory contains an intentionally isolated subset of
[jenniferdavid/Gazebo_Harbour_Models](https://github.com/jenniferdavid/Gazebo_Harbour_Models).

- Upstream revision: `f977dc0d99bf76a46138b22afc47729bae1991cc`
- Upstream license: GNU General Public License v3.0 (`LICENSE`)
- Imported: 2026-06-20
- Local modifications: Godot-compatible derived Collada files described below

The files remain under GPLv3 and are not covered by the MIT license used by
the original Open RTS code. Keep this directory, its license, and its source
files together when redistributing the models.

## Included models

| Upstream directory | Simulator use |
| --- | --- |
| `gazebo_models/container20` | 20-foot container |
| `gazebo_models/container40` | 40-foot container |
| `idle_at2` | Terminal truck (`TT`) |
| `gazebo_models/strad` | Shuttle/straddle carrier (`SC`) |
| `gazebo_models/crane1` | Rubber-tyred gantry crane (`RTG`) |
| `gazebo_models/crane2` | Provisional generic crane for ship-to-shore (`STS`) |

`crane2` is not identified upstream as an STS crane. Its STS mapping is a
temporary visual placeholder and should be replaced by an original model.

## Godot Collada preparation

Godot 4 cannot import Collada nodes containing multiple direct
`instance_*` children. Complete upstream sources are retained under
`upstream_source/`, which contains a `.gdignore` to keep Godot from importing
them. Engine-ready files live under `runtime/`.

`tools/prepare_collada.py` generates `model.godot.dae` variants by expanding
SketchUp `library_nodes` references into the visual scene and moving each
additional geometry instance into an identity child node. The geometry,
materials, and transforms are otherwise unchanged.

Derived files currently exist for `strad` and `crane1`.

Because those Collada scenes contain hundreds of small mesh nodes,
`tools/merge_godot_scene.gd` flattens each prepared scene into a Godot
`ArrayMesh` resource grouped by material. The simulator loads the resulting
`model.godot.res` files, reducing each CHE model to one mesh node.

The upstream README is preserved as `UPSTREAM_README.md`. It requests citation
of the Cargo-ANTS paper listed there.
