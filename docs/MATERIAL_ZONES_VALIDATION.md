# Material Zones implementation and validation

Implementation validation date: 2026-10-05. Publication subsequently authorized as Dev 2.9.11.

## 1. Storage architecture

`cloth_next_material_zone` is a Blender-native FACE/INT mesh attribute. Zone
definitions use saved RNA collections on `Object.cloth_next`. Each definition
has a UUID, an integer token, name, enabled state, expansion state, cached face
count and a compact collection of property overrides. Mesh ID, ordered
topology digest and monotonic next-token counter are mesh-owned ID properties.

## 2. Hard ownership

Each attribute entry is either zero (Base) or one stable zone token. Assignment
replaces that integer, so a face cannot have overlapping owners. Shift removal
sets zero only when the active zone owns the face. Deletion clears the zone's
token. UUID/token identity does not depend on collection order. The artist must
make a shared/linked mesh local and single-user before changing ownership.

## 3. Polygon → triangle mapping

Export uses the existing `Mesh.loop_triangles` ordering and each triangle's
`polygon_index`. Every child of a quad or N-gon gets its source polygon's zone.
The adapter verifies the full exported triangle connectivity/order against the
authored mesh; it refuses an unprovable modifier mapping.

## 4. Exact solver parameters

Enabled overrides expand into `face_material_params`, one float32 solver-unit
value per exported triangle for each used key. Unowned faces and zones missing
that key inherit the base scalar. Inherited friction can retain the existing
Friction Regions output. No vertex membership is constructed or averaged.

Schema validation checks key allowlisting, lengths, finite numbers and ranges.
The authored model additionally checks IDs, duplicate keys/tokens/UUIDs and
orphan ownership. Artist-facing errors name objects/zones/properties and give a
remediation. Unsupported frontend selection refuses the Bake. Normal scenes
omit the new wire field, and existing friction behavior remains intact.

## 5. Friction overlay generalization

For the verified managed overlay frontends, the existing decoder/friction
extension now retains an audited dictionary of triangle arrays. Scalar
expansion substitutes each requested array directly. The Gaia spatial-map
expansion also distinguishes this hard path and rejects conflicting spatial
maps. Legacy v9 patch text upgrades in place; the internal overlay marker is
v10. This marker is not an addon or solver release version.

Official Gaia 0.23 keeps its frontend unchanged. The existing CNX-owned
official-scene bridge extracts the arrays into its binding document. Its
owned subprocess matches native reordered triangles through the official
vertex map and replaces the audited `bin/param/tri-*.bin` input arrays before
simulation. All parameter bindings are validated before any zone file is
replaced. Legacy `face_friction` is applied first; explicit zone friction takes
precedence. No upstream repository source or redistributed solver is changed.

## 6. Supported zone properties

- Stretch Resistance (`young-mod`)
- Bend Resistance (`bend`)
- Friction (`friction`, existing artist-to-solver calibration)
- Shape Damping (`deformation-damping`)
- Fold Damping (`bending-damping`)

The local pinned/installed source audit covers the native triangle tables,
scalar expansion, and element reader. It specifically confirms that upstream
spatial maps reduce vertex weights and therefore are unsuitable here.

## 7. Intentionally unsupported properties

Pressure, Sewing, Shrink, Collision Gap, Surface Offset, Solver Model,
Density/Surface Weight, and Poisson/Sideways Response remain on the base
material. Stretch Limit, plasticity and anisotropic bending are not included
in this first tested zone property set. Some omitted PPF keys can be present
in triangle tables, but they have no tested zone UX/compatibility contract in
this implementation. They are not described as physically global when the
core supports element-level values. Zones do not claim to apply full presets.

## 8. Viewport selector

One modal Object Mode operator owns two temporary draw handlers. It stages
face ownership in memory; LMB adds, Shift+LMB removes, wheel changes only
radius, Enter commits, and Esc discards. Regular middle-mouse navigation passes
through. Available surface-center dots, brush candidate dots, translucent
assigned faces and a screen-space radius circle explain the trigger rule.
Non-planar/concave median centers are projected onto their own tessellation.

## 9. No-through selection

Evaluated visible scene instances supply one combined world-space BVH built
from Blender's exact evaluated loop triangles. Candidate center
projection creates a view ray with Blender's perspective/orthographic helpers.
The nearest BVH hit must identify the target polygon and lie within a small
mesh-scale numerical tolerance of its trigger point. Clipped centers are
excluded. Face-normal direction never substitutes for visibility. Other folds,
visible objects and instances occlude the target. CPU selection never reads
Blender's X-Ray state. The GPU overlay supplies its own opaque-surface depth
prepass so hidden dots also remain hidden in X-Ray view.

## 10. Performance

Face centers, tessellation and BVHs are cached for the modal session. Projection
and 64-pixel spatial bins refresh when the view transform/region size changes.
Each candidate needs one scene raycast rather than one per visible object.
Only centers inside queried brush bins need visibility tests; results are
cached until the view/geometry changes. Evaluated geometry rebuilds only after
relevant depsgraph changes. Static GPU batches are reused; assignment changes
invalidate only the selected-face batch. Panel drawing reads saved definitions,
counts and status, without mesh scans or solver communication.

## 11. Topology and lifecycle

Ordered vertex-count/polygon connectivity hashes and mesh identity are checked
in explicit authoring/validation/export operations. Changed topology requires
Clear All Zone Selections and reselection; old indices are never deliberately
remapped onto new faces. Constant-connectivity deformation is supported.
External ownership changes during the selector cancel that transaction.

Enter, Esc, mode/mesh/object changes, exceptions, file load and unregister
remove both handlers, detach the depsgraph observer, and discard caches and
Blender references. A reload-safe session registry lets registration dispose
old sessions. No feature timer, background thread or import-time drawing is
installed. Dirty state and cached ownership/definition fingerprints invalidate
old Bake matches. Bake ownership gates UI controls and mutation operators.

## 12. Files changed

| Files | Responsibility |
|---|---|
| `cloth_next/materials/zones.py` | Pure ownership, validation, triangle expansion, screen bins, visibility helper |
| `cloth_next/blender/material_zones.py` | Mesh/RNA adapter, operators, compact UI, capability/export/fingerprint integration |
| `cloth_next/blender/material_zone_selector.py` | Modal selection, evaluated BVHs, GPU overlay, lifecycle |
| `cloth_next/blender/object_properties.py` | Persistent zone/override definitions |
| `cloth_next/blender/physics_ui.py` | Material panel footer |
| `cloth_next/blender/registration.py` | Class and lifecycle registration |
| `cloth_next/blender/solver_test.py` | Validation, cache identity, single/multi-object export and capability gates |
| `cloth_next/ppf/schema/data.py` | Optional validated hard triangle arrays |
| `cloth_next/ppf/solver_overlay.py` | Generic hard-array expansion and legacy overlay migration |
| `cloth_next/ppf/official_scene_bridge.py` | Official binding document and audited keys |
| `cloth_next/ppf/official_scene_worker.py` | Validated native triangle array binding/writes |
| `cloth_next/ppf_run/session.py` | Dispatch bridge for zones as well as seams/friction |
| `tests/test_material_zones.py` | Focused regressions |
| `tests/test_phase3b_material_ui.py` | Recording layout supports section separator |
| `tests/integration/test_real_ppf_material_zones.py` | Opt-in real-backend table regression |
| `tools/blender_material_zones_smoke.py` | Headless Blender data/geometry verification |
| `tools/blender_material_zones_viewport_smoke.py` | Separate live-window GPU/modal verification |
| `tools/run_ppf_material_zones.py`, `tools/ppf_material_zones_worker.py` | Real PPF frontend/FixedSession verification |
| `README.md`, `docs/MATERIAL_ZONES.md`, this report | User instructions and implementation evidence |

Pre-existing unrelated water-flow audit files were left untouched.

## 13. Tests added

Focused tests cover ownership transfer/removal/order, exact adjacent-face
tables, quad/N-gon inheritance, disabled zones, legacy friction inheritance,
invalid IDs/keys/ranges/lengths, unchanged no-zone payloads, circle-center-only
selection, first-hit visibility, topology signatures, UI actions/counts/role,
staged Enter/Esc behavior, Shift removal, wheel-only radius, cleanup and
exceptions/load/unregister, Bake locks, capability gates, actual managed
expansion code, and reordered native input writes.

Blender scripts additionally test real RNA/attributes and save/reload, shared
mesh refusal, actual quad/N-gon tessellation, occlusion by same-mesh layers and
another object, reversed normals, both projection helpers, and live GPU/modal
sessions with X-Ray enabled.

## 14. Executed checks

- Full repository pytest: **2,146 passed, 20 skipped, 3 deselected**. Skips cover
  unconfigured real-solver tests and platform/release-specific checks. The
  separately configured Material Zones real-backend test passed.
- Focused material/UI/schema/overlay/official bridge/performance suite:
  **166 passed** before the final extra capability test; dedicated zone suite
  subsequently **24 passed**.
- Final focused zones/solver UI/schema suite: **232 passed** after the storage
  remediation and combined-scene BVH changes.
- Ruff on new modules, worker and test tools: **passed**.
- `git diff --check`: **passed** (Git reports existing LF/CRLF conversion notices).
- Blender **5.2.2 LTS** headless script: **passed**, including saved RNA/attributes,
  quad/N-gon mapping, shared/topology refusal, and real evaluated BVHs.
- Blender **5.2.2 LTS** live-window script: **passed**. Both ORTHO and PERSP with
  X-Ray enabled selected only the front face; actual GPU batches drew; Shift,
  wheel, Esc/Enter and handler cleanup passed.
- Configured real-backend pytest: **1 passed** against official Gaia 0.23.

## 15. Real solver proof

Installed official release **2026-09-27-20-44 / protocol 0.23** built an actual
`FixedScene` and saved `FixedSession` through its public API. The production
CNX native array binder was applied to its reordered triangle inputs. The
saved scene table and the exported float32 native inputs agree exactly:

| Key | Triangle A (Base) | Triangle B (Zone) |
|---|---:|---:|
| `bend` | **10.0** | **100.0** |
| `young-mod` | 1000.0 | 2000.0 |
| `friction` | float32(0.1) | 0.5 |
| `deformation-damping` | float32(0.01) | float32(0.02) |
| `bending-damping` | float32(0.03) | float32(0.04) |

Triangles A `(0, 1, 2)` and B `(1, 3, 2)` share vertices **1 and 2**. Bend
contains exactly **10 and 100**, with **no 40/70/intermediate boundary value**.
The managed expansion regression independently executes the actual overlay
replacement and asserts the same exact table.

Local artifacts: `.tmp_verify/material-zones-native/material-zones-report.json`,
`.tmp_verify/material-zones-native/frontend.log`,
`.tmp_verify/material-zones-viewport.json`, and
`.tmp_verify/material-zones-smoke.blend`. The integration pytest keeps its
separate proof under `.tmp_verify/material-zones-pytest`.

## 16. Remaining limits

- Only the five listed properties are offered; no partial preset claims.
- Topology changes require explicit reselection. No speculative face remapping.
- The selector uses the first 3D Viewport in the active window; quad-view subregion
  authoring is not separately supported.
- Linked/shared mesh ownership mutation and external solver Bake use are refused.
- No production-scale mesh benchmark or long cloth simulation was performed.
- Real validation proves the built native tables/saved session. A control-server
  end-to-end attempt encountered the installed frontend's directory creation
  failure through a Windows project junction (`.cash` / session). The successful
  native test uses the official public authoring/build API at owned resolved
  paths and exercises the same production binder; it does not modify the
  external frontend to bypass that failure.
- Implementation validation did not change release versions or publish artifacts.
  Dev 2.9.11 publication was subsequently authorized separately. The upstream solver
  repository remains unchanged.
