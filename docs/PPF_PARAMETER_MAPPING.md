# PPF parameter mapping

## Static vertex-group Pinning (Phase 3C.1)

Phase 3C.2 adds Follow Animation using the pinned PPF 0.11 contract at upstream revision `7193f158e3843597070f66cb29af19efd9bdcff7`. Each pinned vertex receives its own `pin_anim` track containing continuous solver-world positions and times `(BlenderFrame - BakeStart) / FPS`. The PPF frontend converts consecutive samples into per-vertex `MoveByOperation` segments. Cloth NeXt emits no Pull, release, Torque, Spin, Scale, or explicit operation entry.

Audited against official PPF commit
`7193f158e3843597070f66cb29af19efd9bdcff7`, specifically
`blender_addon/core/encoder/mesh.py`, `core/encoder/pin.py`,
`frontend/_decoder_.py`, and `frontend/_scene_.py`. Selected indices are the
SHELL object's `pin` array. Matching per-vertex `pin_config` entries carry a
deterministic `pin_group_id` and zero operations; the frontend creates one hard
holder and writes `bin/pin-ind-0.bin` as native `uint64`. It writes no Pull or
operation binary for this static contract.

Membership is binary at `STATIC_PIN_WEIGHT_THRESHOLD = 1e-6`. Targets are the
same evaluated local positions uploaded at Bake Start. The immutable Cloth NeXt
domain config records no operations or release, linear transition, zero Pull,
stiffness 1.0, no pull weights, and no rest-shape tracking. Animated operations,
Pull, and timed release remain unsupported.

## Bake frame mapping

For Blender range `S–E`, frame `S` is uploaded as the initial state. PPF
receives `frames = E - S`; solver step `n` maps to Blender frame `S+n`.
Playback contains `E-S+1` samples and Mesh Cache sample zero is placed at `S`.
Zero-step runs are not verified by the pinned PPF build, so End must exceed
Start.

Status: Phase 3B. Every mapped field below is implemented, validated, and
covered by exact encoder tests plus real pinned-solver integration runs.
Everything not listed as **mapped** is unsupported and is not shown as an
editable control.

All wire keys are the upstream kebab-case spellings of the pinned commit
`7193f158e3843597070f66cb29af19efd9bdcff7` (`blender_addon/core/encoder/params.py`,
`kinds/param.rs`). Material floats are rounded through IEEE-754 float32
before the CBOR encode, mirroring the upstream encoder's `np.float32(...)`
wrapping.

## Stiffness basis (critical)

The PPF wire value `young-mod` is **density-normalized** (the solver's
native convention; the bundled fabric presets are calibrated in it). Cloth
NeXt's *Stretch Resistance* stores this direct wire value:

- It is **never divided by density** anywhere in Cloth NeXt.
- It is **not** an ordinary textbook Young's modulus in pascals and is
  never labeled plain "Pa".
- There is no user toggle between physical pascals and normalized values;
  one unambiguous representation is used everywhere. Advanced PPF labels it
  "density-normalized PPF young-mod".
- The Phase-3A helper `normalized_young_modulus()` was removed; a
  regression test (`test_stretch_resistance_is_never_divided_by_density`,
  `test_no_double_normalization_helper_exists`) prevents accidental double
  normalization from returning.

## Shell (Cloth role) — mapped

| UI name | Internal property | Pure dataclass field | PPF key | Unit | Range (hard) | Conversion | Default | Preset-controlled |
|---|---|---|---|---|---|---|---|---|
| Material Preset | `material.preset` | — (preset id) | — | — | 37 bundled ids + `CUSTOM` | categorized selection applies mapped values | `DEFAULT_CLOTH` | research method in `MATERIAL_LIBRARY.md` |
| Solver Model | `material.model` | `model` | `model` | enum | `FABRIC`, `SHAPE_PRESERVING` | `FABRIC`→`baraff-witkin`, `SHAPE_PRESERVING`→`arap` | `FABRIC` | yes |
| Surface Weight | `material.surface_weight` | `surface_weight` | `density` | kg/m² (area density — **not** kg/m³) | > 0 … 10000 | direct float32 | 1.0 | yes |
| Stretch Resistance | `material.stretch_resistance` | `stretch_resistance` | `young-mod` | density-normalized (soft max 100000) | 0 … 1e9 | direct float32, **no density division** | 1000.0 | yes |
| Sideways Response | `material.sideways_response` | `sideways_response` | `poiss-rat` | — | 0 … 0.4999 | direct float32 | 0.35 | yes |
| Bend Resistance | `material.bend_resistance` | `bend_resistance` | `bend` | — (soft max 100) | ≥ 0 | direct float32 | 10.0 | yes |
| Stretch Limit | `material.stretch_limit_enabled` | `stretch_limit_enabled` | `strain-limit` enable | bool | — | disabled ⇒ wire value 0.0 | off | yes |
| Maximum Stretch | `material.maximum_stretch_percent` | `maximum_stretch_percent` | `strain-limit` | % (soft max 20) | > 0 … 100 | percent / 100 when enabled | 5.0 % | yes |
| Shape Damping | `damping.shape_damping` | `shape_damping` | `deformation-damping` | seconds (soft max 0.1) | ≥ 0 | direct float32 | 0.0 | yes |
| Fold Damping | `damping.fold_damping` | `fold_damping` | `bending-damping` | seconds (soft max 0.1) | ≥ 0 | direct float32 | 0.0 | yes |
| Friction | `collision.surface_grip` | `surface_grip` | `friction` | coefficient | 0 … 1 | direct float32 | 0.5 | yes |
| Collision Gap | `collision.collision_gap` | `collision_gap` | `contact-gap` | Blender world units (soft max 0.01) | ≥ 0 | direct float32 | 0.001 | yes |
| Surface Offset | `collision.surface_offset` | `surface_offset` | `contact-offset` | Blender world units (soft max 0.03) | ≥ 0 | direct float32 | 0.0 | yes |
| Enable Contact | `collision.enabled` | (`contact_enabled` argument) | `scene.disable-contact` | bool | — | enabled ⇒ `false`, disabled ⇒ `true` | on | no |

Pure dataclass: `cloth_next.materials.ShellMaterialSettings` (immutable,
validated on construction; no `bpy`, no paths, no processes).

## Static (Collider role) — mapped

| UI name | Internal property | Pure dataclass field | PPF key | Unit | Range | Conversion | Default |
|---|---|---|---|---|---|---|---|
| Friction | `collision.surface_grip` | `surface_grip` | `friction` | coefficient | 0 … 1 | direct float32 | 0.5 |
| Collision Gap | `collision.collision_gap` | `collision_gap` | `contact-gap` | world units | ≥ 0 | direct float32 | 0.001 |
| Surface Offset | `collision.surface_offset` | `surface_offset` | `contact-offset` | world units | ≥ 0 | direct float32 | 0.0 |

`friction`, `contact-gap`, and `contact-offset` are the only keys the
upstream encoder emits for STATIC groups. Pure dataclass:
`cloth_next.materials.StaticMaterialSettings`. Cloth and collider values
map independently — the cloth's SHELL group and the collider's STATIC group
each carry their own friction/gap/offset.

## Scene — mapped

| Value | PPF key | Source | Notes |
|---|---|---|---|
| Time Step | Scene `cloth_next_quality.time_step` | `dt` | float32 seconds; default `0.001`, range `0.001..0.01` |
| Minimum Newton Steps | Scene `cloth_next_quality.min_newton_steps` | `min-newton-steps` | int; default `1`, range `1..64` |
| PCG Max Iterations | Scene `cloth_next_quality.cg_max_iter` | `cg-max-iter` | int; default `10000`, range `100..100000` |
| PCG Tolerance | Scene `cloth_next_quality.cg_tol` | `cg-tol` | float32; default `0.001`, range `0.00001..0.1` |
| Enable Pressure / Pressure | Object `pressure.enable_inflate` / `pressure.inflate_pressure` | `pressure` (SHELL only) | float32; configured non-negative value when enabled, otherwise `0.0` |
| Gravity | `gravity` | Sum of Gravity Empty local `-Z` vectors, or Blender scene gravity when no Gravity Empty is enabled | axis-swapped to solver Y-up |
| Wind Force Empty | `wind` | Sum of enabled Wind Empty local `+Z` vectors | axis-swapped to solver Y-up |
| Air Density Force Empty | `air-density` | Sum of enabled Air Density Empty values | solver aerodynamic density |
| Air Friction Force Empty | `air-friction` | Sum of enabled Air Friction Empty values | tangential drag/lift ratio |
| Vertex Air Damping Force Empty | `isotropic-air-friction` | Sum of enabled Vertex Air Damping Empty values | isotropic per-vertex damping |
| Animated Forces | `dyn_param` | Native Blender keyframes sampled once per output frame | PPF linearly interpolates the samples during solver substeps |
| Frame count | `frames` | Blender frames `N-1` | Blender 1..N → solver 0..N-1; development slice N=8 |
| FPS | `fps` | Blender scene FPS | frame→time conversion |
| Friction mode | `friction-mode` | fixed `"min"` | Minimum combination: both touching surfaces need high grip; shown read-only in Advanced PPF |
| Contact | `disable-contact` | cloth's Enable Contact | inverted boolean |

## Presets

Bundled read-only source: `cloth_next/materials/ppf_fabric_presets.toml`
(provenance: `st-tech/ppf-contact-solver` @ `7193f158…`,
`blender_addon/presets/materials.toml`, Apache-2.0 — see
`docs/THIRD_PARTY_NOTICES.md`). Parsed and validated once at registration;
malformed data raises a visible error, applies nothing, and degrades the
selector to Custom. Selecting a preset applies its mapped values
immediately; manually changing any preset-controlled value switches the
selection to **Custom** without resetting anything. New Cloth NeXt objects
default to **Default Cloth** (the pinned upstream shell defaults, not an
upstream calibrated textile).

| Preset | density | young-mod | poiss-rat | bend | friction | strain-limit |
|---|---|---|---|---|---|---|
| Default Cloth | 1.0 | 1000 | 0.35 | 10.0 | 0.5 | disabled |
| Silk | 1.0 | 500 | 0.4 | 1.42 | 0.25 | 6 % |
| Flag | 1.0 | 1000 | 0.4 | 0.83 | 0.30 | 4 % |
| Cotton | 1.0 | 5500 | 0.35 | 4.3 | 0.35 | 5 % |
| Wool | 1.0 | 2000 | 0.4 | 3.67 | 0.40 | 8 % |
| Denim | 1.0 | 10000 | 0.25 | 10.0 | 0.50 | 3 % |
| Leather | 1.0 | 13000 | 0.4 | 1.8 | 0.50 | 2 % |

## Migration from the Phase-2.8 placeholder properties

The old Quality, Physical (Total Mass / Thickness / Stretch / Shear / Bend
stiffness), per-mode Damping, Self Collision, Pressure, Shape, and editable
Cache-range properties were **never read by any solver**. They are not
converted — in particular, old Stretch/Shear values are *not*
mathematically reinterpreted as PPF `young-mod` — and remain as orphaned,
never-read ID properties inside old `.blend` files. Existing Cloth
NeXt-enabled objects keep their enabled state, Cloth/Collider role, and any
existing cache result/modifier, and start from the DEFAULT CLOTH material.
Geometry, materials, vertex groups, and files are untouched.

## Cache invalidation metadata

Every completed developer-slice run records a versioned SHA-256 fingerprint
of all mapped material values (preset id, model, density, stretch,
sideways, bend, both dampings, cloth and collider friction/gap/offset,
stretch-limit state and value, contact enabled) on the cloth object and in
a `*.meta.json` sidecar next to the PC2 cache. When current settings no
longer match, the Cache panel marks the result stale; nothing is deleted
automatically — rebake or Clear explicitly. A full production cache
metadata system remains Phase-4 work.

## Not mapped (hidden, not editable)

Target volume/compressibility/gas pressure, shrink, stitching, plasticity,
dynamic material-parameter animation, collision windows, sand, PDRD,
arbitrary frame ranges, tearing, and live preview. No stored or wire-level
`substeps` value exists: `dt` is the sole
authoritative time-resolution setting. No placeholder
Stretch/Shear keys are ever emitted; the encoder sends exactly the audited
key set and nothing else.

## Source

Derived from the official
[parameter encoder](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/blender_addon/core/encoder/params.py),
[property definitions](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/blender_addon/ui/object_group.py),
and [preset data](https://github.com/st-tech/ppf-contact-solver/blob/7193f158e3843597070f66cb29af19efd9bdcff7/blender_addon/presets/materials.toml).
Cloth NeXt implements its own encoder from the server schema and test
vectors; no official add-on code is copied or imported at runtime.
