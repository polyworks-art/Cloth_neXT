# Local Wind and timeline preview

This feature is prepared on `codex/wind-vector-overlay`; it is not part of the published 2.9.17 package.

Enable **Local Wind Field** on a Cloth NeXt Force Empty. Wind follows its local +Z direction and fades to zero at **Radius**. **Falloff** concentrates the flow around the Empty. **Turbulence** adds bounded, spatially varying swirling air velocity; **Swirl Size** sets its spatial wavelength and the existing **Noise Scale** sets its temporal period. Animate the Empty or these physical properties normally.

Enable **Show Wind Vectors** and scrub or play the timeline before baking. The overlay reads the current evaluated transforms and animated values, then uses the same local velocity evaluator as export. It never changes the timeline, creates a Bake cache or starts a solver. **Vector Scale** affects display only. The preview draws combined local fields plus legacy global Wind around visible Force Emptys, using bounded float32 arrows and Blender's overlay visibility toggle.

Local fields require official GAIA 0.23 and positive Air Density. They are exported as `air-velocity` grids, retain normal aerodynamic drag, and do not also contribute to the global Wind vector. Legacy Wind remains unchanged when Local Wind Field is off. Existing Water Flow streaming retains these grids as base fields when replacing its held windows.

Every output frame is sampled with its actual Blender pose and values; the original frame and subframe are restored even on cancellation. Bounds cover the moving source across the range, spacing follows the smallest source radius and selected Field Resolution, and combined uncompressed field schedules are limited to 32 MiB before allocation. Large source motion can increase grid size substantially. The solver interpolates grid nodes in space and samples in time: the viewport evaluator is continuous, so very coarse grids or rapid changes between frames can differ from the preview. Raise Field Resolution for small swirls.

The native global Wind has its own multiplier, `0.875 + 0.125*sin(30*t)`, with `t` relative to Bake Start. Authored air-velocity fields bypass it. Replacing existing global Wind with a local field therefore intentionally changes its temporal response; the implementation preserves the legacy path and applies this multiplier only to the global contribution in the preview.

Validation on 2026-10-12:

- Pure tests cover finite inputs, exact support boundaries, translation, direction, superposition, bounded deterministic turbulence, float32 preview arrows and byte-exact agreement with the existing official grid axis conversion.
- Real Blender tests cover pre-Bake preview, keyframed location and strength, temporal turbulence, frame/subframe restoration, rejected older protocols and duplicate-free load/frame handlers.
- Two actual GPU captures at frames 1 and 60 contain visible Wind arrows and different pixels.
- A native CUDA test consumed the grid exported by Blender: mean displacement inside the source was 0.14659; maximum displacement outside was 4.47e-8.
- Earlier 20-frame native comparisons confirmed group targeting and spatially different cloth motion. Equal nominal global/grid Wind speeds were deliberately not treated as equivalent because of the native global gust multiplier.

The current implementation uses Cloth NeXt Force Emptys. It does not import arbitrary Blender native Force, Vortex or Turbulence objects. Obstacle shielding, CFD, and per-object field authoring are outside this change.

Upstream contract: [GAIA Force Fields](https://github.com/st-tech/ppf-contact-solver/blob/344bcb906203db19f3ea59ccd011d5a297f90390/docs/blender_addon/workflow/params/force_fields.md).
