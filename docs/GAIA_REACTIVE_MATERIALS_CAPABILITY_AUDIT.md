# GAIA Reactive Materials: official 0.23 capability gate

Audit date: 2026-10-01. Status: **Phase A complete for the decisive solver
contract; live reactive material physics blocked. Product feature not complete.**

Inspected the separate, unchanged official source checkout at
`../ppf-contact-solver`, commit `344bcb906203db19f3ea59ccd011d5a297f90390`
(release `2026-09-27-20-44`, protocol 0.23, schema 2). Its working tree is clean.
This is source evidence, not a new CUDA runtime validation.

## Decision

Do not enable Dynamic Wetness as simulated reactive material physics in this
release. The official live-input contract cannot apply newly computed spatial
material responses between frames. Density animation is independently refused.
The requested water absorption changing local mass, stiffness, friction and
damping from current simulated positions therefore has no verified correct
implementation through that contract. Stop that sub-feature under the user's
immutable-solver rule. No runtime architecture or product controls were added.

## Capability matrix

| GAIA requirement | Official mechanism | Dynamic support | Rebuild/start boundary | Cloth NeXt path |
| --- | --- | --- | --- | --- |
| Read current simulated positions | `FixedSession.get.vertex(frame)` after `run_until_frame` / `held_frame` | Yes, frame output | None | Sample the held output, respecting vertex mapping and coordinate conversion |
| Advance and synchronize | `run_until_frame`, `step_frame`, `release`; `hold_at_frame`, `held` | Yes | None | Public frontend worker could own stepping; existing TCMD start must not race it |
| Live input signal | Public `update_force_field` / `update_params` write `inputs_updated` | Yes, restricted inputs | None | Use public methods; marker is not a generic scene reload |
| Spatial Young's modulus | `set_param_spatial("young-mod", weights, target)`; `set_param_spatial_anim` | Prebuilt animation only; no held reload | Build/load to install new maps | Current-position feedback deferred |
| Spatial bending | Maps / animation for `bend`, `bend-warp`, `bend-weft` | Same restriction | Build/load | Deferred |
| Spatial friction | Maps / animation for `friction` | Same restriction | Build/load | Existing build-time face friction does not establish live support |
| Spatial deformation damping | Maps / animation for `deformation-damping` | Same restriction | Build/load | Deferred |
| Spatial bending damping | Maps / animation for `bending-damping` | Same restriction | Build/load | Deferred |
| Effective density / mass | Build-time material density; density absent from animation whitelist | Explicitly unsupported animation | Build/initialize; continuation semantics unproved | Deferred; no force-based mass substitute |
| Scene schedules | `update_params`, `dyn_param.txt` | Selected scene keys and velocity schedules | None for allowed schedules | Can use gravity, wind, air-density, air-friction, isotropic-air-friction, dt, playback; source also lists inactive-momentum |
| Collision windows | Built group window table | Mid-run changes explicitly rejected | Initialization | Do not alter while held |
| Sampled environmental acceleration | `ForceField.grid` / `sample`; `update_force_field` | Yes | None for source replacement | Independent fluid-force implementation possible with reliable provider data |
| Sampled air velocity | Grid kind `air-velocity` | Yes | None for source replacement | Air model is not automatically liquid drag |
| Force group targeting | Built group context / masks | Sources resolved against built groups | Group membership/weights stay built | Bind supported targets before launch |
| `force-field-weight` | Built per-object weight | Public update explicitly keeps built weights | Build for new weights | Do not claim reactive per-object weight updates |
| Solver statistics | Official frontend statistics accessors / output | Inspection | None | Diagnostics; cannot mutate material state |
| Cancel/resume | Held save-and-quit saves solver state; held itself is not a checkpoint | Solver supported | Resume boundary | Coupled GAIA checkpoint implementation and validation deferred |
| Shader/debug/cache state | Cloth NeXt / Blender responsibility | Feasible independently | No solver rebuild for display | Deferred until supported physics scope; shader-only output is not completion |
| Baked FLIP provider | External baked data, not solver memory | Not audited in this gate | Provider dependent | Availability, occupancy, velocity, cache fingerprints require separate evidence; no fabricated fields |

## Exact evidence

Links below pin the inspected revision rather than a moving branch.

- [Public held-frame API](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_session_.py#L955):
  `run_until_frame` waits after the requested frame is written. An active held
  run retains its device and buffers. `step_frame` requests a later boundary;
  the final frame finishes rather than holding. Hold timeout saves and quits.
- [Actual hold reload](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/backend.rs#L828):
  upon release, `inputs_updated` causes only `scene.reload_dyn_param` and force
  field loading/installation. It does not reload static material arrays,
  `bin/param_anim`, mass tables or material keyframes.
- [Public update restrictions](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_session_.py#L1115):
  `update_force_field` preserves built weights and group context.
  `update_params` re-exports scene schedules, not object material maps.
- [Scene dynamic key whitelist](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/scene.rs#L3036):
  no Young's modulus, bend, friction, material damping or density key.
  `reload_dyn_param` at line 3296 replaces that table and refuses changed
  collision windows; it does not call the material schedule reader.
- [Material schedule reader and whitelist](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/scene.rs#L643):
  density is excluded and the reader explicitly rejects it because mass-change
  semantics are undecided. `read_param_anim` is called during scene loading
  (line 1702), not the held reload.
- [Cached material keyframes](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/crates/ppf-cts-solver/src/backend.rs#L1130):
  keyframes and derived hinge/edge/vertex tables are assembled once and then
  blended. Rewriting schedule files while held cannot update this cache.
- [Official map representation](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_scene_object_.py#L199):
  weights are per object vertex, finite and bounded in [0,1]. Effective values
  interpolate base toward target; element weights are vertex means.
  [Export](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/frontend/_scene_.py#L1299)
  stores animated triangle maps as float32, flattened keyframe-major arrays in
  `bin/param_anim/tri-<key>.bin`, with float64 `times.bin`. This existing
  representation should be reused if live material support becomes available.

## Alternatives assessed

1. **Precompute animation:** official and valid for authored schedules, but
   future water contact is unknown until the coupled cloth trajectory evolves.
   Sampling initial positions or a preliminary dry bake fails the moving-cloth
   and feedback requirements. It is not an equivalent implementation.
2. **Rewrite input binaries while held:** no reader reloads material tables;
   changed files do not change live device properties. Rejected.
3. **Use environmental forces instead:** supported for independent acceleration,
   but cannot reproduce local constitutive stiffness, contact friction, material
   damping or inertial mass. Rejected as a material-response substitute.
4. **Save, rebuild and resume every frame:** not established as a semantics-safe
   alternative. `preserve_output=True` retains outputs, not a public guarantee
   of material/mass-change continuation consistency. New initialization and
   checkpoint compatibility would need independent proof, particularly mass,
   momentum and derived property tables. Do not enable this speculative path.

## Final phase status (requested report items)

Architecture and state representation (1,4): not introduced before capability
gate resolution. A future design should separate providers, persistent spatial
state, bounded response mappings and a centralized official map adapter.

Capabilities and live loop (2,3): audited above. Official loop is write frame,
hold, read current positions, update supported live inputs, request next frame,
reload on release, advance. Material feedback is unsupported in that loop.

Provider, surface flow, capillary spread, drying (5–8): not implemented.
Material responses/maps (9,10): encoding identified; runtime feedback deferred.
Shader attributes and overlay (11,12): not implemented. Cache/recovery (13):
not altered; synchronized reactive resume remains unvalidated.

Real CUDA validation (14): no new reactive CUDA run; the requested three scenes
cannot pass their physical-response criteria using the audited held contract.
Regression (15): no new test suite run for this documentation-only change;
2045 passed / 19 skipped / 3 deselected remains the user-supplied prerequisite
baseline, not a fresh result. Performance (16): no runtime changes or allocations;
no new performance measurement. Unsupported/deferred (17): reactive materials,
dynamic wetness product integration and dependent validation. Independent fluid
forces remain feasible but unimplemented and provider support is unverified.

Solver integrity (18): all solver inspection was read-only; separate upstream
working tree remains clean. No solver source, frontend, binaries, protocol or
schema was changed. Existing Cloth NeXt prerequisite edits were preserved.
No release, install replacement, publication or registry change was performed.
