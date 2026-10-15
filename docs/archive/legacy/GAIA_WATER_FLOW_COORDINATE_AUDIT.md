# GAIA Water Flow coordinate audit — 2.9.9

Scope: existing velocity reconstruction and display, using frame 150 of the supplied GAIA_Watertest scene. No solver or FLIP source was modified, and the original scene was not saved.

## Findings and fixes

Forced FLIP cache imports changed object transforms without evaluating the view layer. Reading matrix_world immediately therefore sampled stale coordinates: the authored scene could acquire a +4 m offset in X and Y. Evaluate before and after importing, settle the cache parenting transform, and use the actual evaluated cache matrix. Grid bounds cover both the domain and cached grid. Animated cache transforms are rejected. The flip-evaluated-world-v2 provenance invalidates older prepared fields; legacy previews are hidden until preparation.

FLIP loader position handling transforms mesh-local coordinates. Its velocity attribute is copied directly from the exported physical velocity data. Transform positions with the cache matrix; preserve exported world-axis velocity components. Post-bake display rotation/scale does not transform those raw velocity attributes. The tested transformed cases are replays of one existing bake, not freshly rebaked rotated simulations.

Source proof: installed FLIP flip_fluid_cache.py mesh transform and velocity attribute loading; official FLIP engine fluidsimulation.cpp position export applies domain scale/offset while velocity export writes the velocity components. See https://github.com/rlguy/Blender-FLIP-Fluids/blob/master/src/engine/fluidsimulation.cpp and https://github.com/rlguy/Blender-FLIP-Fluids/wiki/Domain-Attributes-and-Data-Settings .

Arrays use [Z,Y,X,3] in Blender world space. Nodes include both bounds: min + index/(N-1)*(max-min), with no half-cell shift. Only the official field encoder converts axes (x,y,z) -> (x,z,-y), reverses the appropriate grid dimension and transforms bounds. The unchanged GAIA force_field.rs and external_field.kernel.cpp contract uses this node layout. Outside bounds returns zero.

The primary rendering failure was the upload of NumPy float64 coordinates into GPU float32 vertex buffers: valid CPU endpoints became malformed GPU positions, producing scene-spanning lines even in the constant +X test. All overlay coordinates now upload as contiguous float32. GPU raster extents are checked against projected CPU endpoints. Display also previously allowed raw speed times display scale to create excessively long lines. Occupied nodes use influence > 0.01. New display lengths are limited to min(1 metre, twice the smallest node spacing); this does not clamp solver velocities. Position-only, normalized direction and constant +X modes isolate origin and direction checks. Four actual GPU draw-function offscreen captures contain visible overlay pixels; interactive user-session acceptance remains separate.

## Numerical cells

Each row compares the weighted source velocity, reconstructed Blender vector and actually encoded GAIA vector. Production payload decompression was checked against the encoder.

| Grid XYZ | World position | Weighted source | Reconstructed | GAIA | Encode error |
|---|---|---|---|---|---|
| [0, 0, 0] | (-4, -4, 0) | (0.0149873, -0.0271362, -0.025642) | (0.0149873, -0.0271362, -0.025642) | (0.0149873, -0.025642, 0.0271362) | 0.0 |
| [22, 6, 0] | (3.65217, -1.91304, 0) | (4.79101, -0.0802504, -0.0240023) | (4.79101, -0.0802504, -0.0240023) | (4.79101, -0.0240023, 0.0802504) | 0.0 |
| [21, 13, 0] | (3.30435, 0.521739, 0) | (4.76484, 0.0501938, -0.023414) | (4.76484, 0.0501938, -0.023414) | (4.76484, -0.023414, -0.0501938) | 0.0 |
| [0, 21, 0] | (-4, 3.30435, 0) | (0.0360033, 0.0259401, -0.0416796) | (0.0360033, 0.0259401, -0.0416796) | (0.0360033, -0.0416796, -0.0259401) | 0.0 |
| [0, 4, 1] | (-4, -2.6087, 0.333333) | (0.00661822, -0.0161449, -0.0693239) | (0.00661822, -0.0161449, -0.0693239) | (0.00661822, -0.0693239, 0.0161449) | 0.0 |
| [22, 10, 1] | (3.65217, -0.521739, 0.333333) | (4.70342, -0.0115996, -0.157408) | (4.70342, -0.0115996, -0.157408) | (4.70342, -0.157408, 0.0115996) | 0.0 |
| [22, 17, 1] | (3.65217, 1.91304, 0.333333) | (4.93967, 0.0230172, -0.0319555) | (4.93967, 0.0230172, -0.0319555) | (4.93967, -0.0319555, -0.0230172) | 0.0 |
| [4, 3, 2] | (-2.6087, -2.95652, 0.666667) | (3.21254, -0.00291316, -2.10125) | (3.21254, -0.00291316, -2.10125) | (3.21254, -2.10125, 0.00291316) | 0.0 |
| [1, 4, 3] | (-3.65217, -2.6087, 1) | (1.15934, -0.00480015, -0.112722) | (1.15934, -0.00480015, -0.112722) | (1.15934, -0.112722, 0.00480015) | 0.0 |
| [1, 23, 4] | (-3.65217, 4, 1.33333) | (4.99903, 0.000272623, -0.000400954) | (4.99903, 0.000272623, -0.000400954) | (4.47525, -0.000358943, -0.000244059) | 0.0 |

## Probes and transformations

- {'kind': 'inside', 'position': [3.304347826086956, -3.6521739130434785, 0.6666666666666666], 'occupancy': 0.9997801780700677, 'water_contribution': [5.627311706542965, 0.01828808709979056, -0.8556213378906244], 'encoded_solver_velocity': [5.627311706542969, -0.855621337890625, -0.018288087099790573], 'difference': 3.552713678800501e-15}
- {'kind': 'outside', 'position': [5.0, 5.0, 5.0], 'occupancy': 0.0, 'water_contribution': [0.0, 0.0, 0.0], 'encoded_solver_velocity': [0.0, 0.0, 0.0], 'difference': 0.0}
- {'kind': 'surface', 'position': [-3.619999885559082, 1.7800002098083496, 1.0199999809265137], 'occupancy': 1.0, 'water_contribution': [1.5022491016027488, 0.014255391518195616, -0.14691122256693365], 'encoded_solver_velocity': [1.5022491016027486, -0.14691122256693365, -0.014255391518195616], 'difference': 2.220446049250313e-16}
- {'kind': 'boundary', 'position': [4.0, 0.0, 0.5], 'occupancy': 0.3505382835865021, 'water_contribution': [0.19431669265031815, 0.19371303543448448, -0.057288119569420815], 'encoded_solver_velocity': [0.19431669265031815, -0.057288119569420815, -0.19371303543448448], 'difference': 0.0}
- authored: bounds {'min': [-4.0, -4.0, 0.0], 'max': [4.0, 4.0, 4.0]}; samples outside domain 0.
- translated: bounds {'min': [1.0, -7.0, 2.0], 'max': [9.0, 1.0, 6.0]}; samples outside domain 0.
- rotated: bounds {'min': [-5.636239528656006, -5.636239528656006, 0.0], 'max': [5.636239528656006, 5.636239528656006, 4.0]}; samples outside domain 0.
- scaled: bounds {'min': [-7.199999809265137, -7.199999809265137, 0.0], 'max': [7.199999809265137, 7.199999809265137, 7.199999809265137]}; samples outside domain 0.

## Unchanged official CUDA solver

Constant +X fixture: inside patch mean X displacement 0.313329667 m; outside patch maximum displacement 2.98146e-9 m. Actual FLIP frame-150 field with original Plane topology translated into supported liquid: mean X displacement 0.062470470 m, peak 0.273387671 m. The latter deliberately has no pins, collider or gravity. This validates response to the reconstructed field, not full original-scene production cancel/resume.

## Additional release corrections and validation

GAIA 0.23 installation failed because a documentation phrase occurred twice; validation now anchors to unique required implementation statements, remaining read-only. Onboarding seen state is durably and atomically stored. GAIA Water Flow is a separate card, its supplied icon is inverted, and preparation uses the GAIA Flow title.

Regression suite: 2080 passed, 7 skipped, 15 deselected before release metadata changes; release-specific checks and targeted regressions also pass. Real Blender UI registration, preparation/reuse/cancellation, transformed samples, raw grid/encoder comparisons and official solver fixtures were exercised. Auxiliary audit data is bundled in dist/water-coordinate-audit.gaia.
