# GAIA Flow progress and NEON playback audit — 2026-10-14

Current fixes are local, uncommitted and not installed or released. Existing adaptive budget and provider changes are retained.

## Actual user bake

Saved scene: `F:/16_Cloth_neXt/SW04/SW04_NEON.blend`, saved 21:21.
Caches: `F:/16_Cloth_neXt/cache_AD/cn_test_cloth_216c98d6c6dd_{00,01,02}.pc2`.
All three contain 250 samples (14946, 4379 and 4031 vertices), exact expected file sizes, and exclusively finite coordinates. 249 samples differ from the first sample for each object.

Maximum single-coordinate changes in local millimeter units are 13.5215, 3.0651 and 7.6000. Object scale is 0.001, with a 90-degree rotation around X. These are small physical changes, not a large fluid-driven displacement.

A read-only Blender 5.2.2 factory-startup evaluation of the saved scene at frames 1, 125 and 250 confirms that all three Mesh Cache modifiers evaluate changing vertices. At frame 250 their local changes match the PC2 data (13.4550, 2.4279, 7.6000). Modifiers are enabled, factor 1, SCENE playback, FRAME time, start 1, scale 1, OVERWRITE, correct finalized cache paths, and no vertex-group mask. No playback freeze was reproduced in the saved scene. The cause of the weak physical motion is not established; a successful cache import does not demonstrate adequate fluid response.

Headless inspection did not register the Character DNA driver namespace and printed its driver errors; the cloth objects have only Mesh Cache and disabled viewport Subdivision modifiers, so this check establishes saved cloth cache playback, not character animation correctness. No scene or cache was written.

Saved settings inspected with registered add-on properties have Flow enabled on all three objects, shared .gaia path, Influence 1, Velocity Scale 1, Coupling Strength 1 and Flow damping 1. Bake metadata confirms GAIA protocol 0.26. Native fields have not yet been independently verified for this particular bake.

## Preparation measurements from bake metadata

- Collider capture: 336.530 s, 250 frame evaluations, 500 collider samples.
- Scene encoding: 1.029 s.
- Mesh extraction: 0.144 s.
- Export preparation: 9.003 s.
- Native fluid encoding had no dedicated timing in this version; do not confuse its cost with the Scene encoding measurement.

Collider cache identity is unsafe for skipping animated evaluation in this scene; both captures were verified cache hits after sampling. No unsafe animation skip or transform-only substitution was introduced.

## Local corrections

- Publish every sample in synchronous shared collider capture instead of just first and last.
- Clear obsolete activity labels when the activity or state changes.
- Show preparation/export progress on the Companion activity bar; avoid stale scene-frame labels during encoding.
- Give native fluid encoding bounded frame progress and a separate `native_fluid_encoding` timing.
- Observe ETA progress per queued solver event using its worker monotonic emission time, rather than only the last event in a UI drain. No fixed 30-frame delay exists.
- Refresh runtime activity during legacy Water Flow streaming even while its boundary file is unchanged. Native 0.26 uses the ordinary simulation loop after file encoding; this legacy correction alone is not evidence that native telemetry is fixed.

## Validation

343 focused tests passed (solver session, solver UI, Bake status, Companion detail/frame progress, native fluid and streaming). Includes a regression showing ETA after the first three progress events even when drained together. The new code has not been tested in the installed Companion or another complete NEON bake. The weak fluid response and the live UI behavior still require validation.
