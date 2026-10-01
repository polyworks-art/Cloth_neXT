# GAIA Water Flow — first implementation

Water Flow reconstructs a time-dependent velocity grid from baked FLIP liquid particles and their `flip_velocity` attribute. It submits the result through GAIA 0.23's existing `air-velocity` force-field interface. Neither GAIA nor FLIP source was modified; no release was built or installed.

## Artist workflow

Enable GAIA Water Flow on an individual Cloth object, select its baked FLIP Domain, choose Influence, Velocity Scale, grid resolution and a `.gaia` cache path, then prepare the cache and run the existing GAIA workflow. Optional debug vectors show the reconstructed contribution. Velocity export must be present in the requested bake frames. Current Cloth FPS must match baked FLIP FPS; animated FPS and explicit retiming are rejected.

Influence scales the reconstructed velocity contribution; Velocity Scale changes its magnitude. GAIA's existing scene air density and air friction govern the resulting relative drag. There is no independent water-only drag coefficient in this implementation. This is an approximation using the official relative air-velocity model, not CFD water pressure, buoyancy or quadratic hydrodynamic drag.

## Field and container

The FLIP adapter reads all liquid particle categories regardless of viewport sampling percentages, restoring its temporary import settings afterward. Positions are transformed into Blender world space; physical world velocities are not transformed twice. A normalized trilinear splat reconstructs velocity and local support. Empty support and positions outside the field contribute zero additional flow. The surface-velocity fallback has near-surface support only and is not a volumetric reconstruction.

The generic adapter rotates world coordinates into GAIA's existing convention and writes its official grid schema. The solver samples the field at current simulated positions, interpolating space and time. No animated rest-position or post-simulation displacement mechanism is used.

`.gaia` is a versioned ZIP container holding the manifest, frame velocity/support arrays, checksums and source fingerprints. Writes replace atomically; cancellation preserves the previous container. Reads enforce size limits and reject corrupt arrays, duplicate members and invalid times. Diagnostic and validation records are bundled in the delivered containers. Native GAIA runtime staging still requires its official temporary files.

Production upload currently uses a preauthored schedule, limited to **32 MiB of uncompressed velocity data across all grids**. Reconstruction is framewise. A tested two-frame held-update adapter exists, but production streaming is not integrated. Shared material groups are rejected to avoid applying an object's flow to other objects.

## Verification with GAIA_Watertest

Source: `F:\GAIA_Watertest.blend` and its adjacent FLIP cache. Frames 1–250 at 24 FPS were converted at resolution 24, using 66,727 liquid samples in the first frame and 589,284 in the last. Conversion took 81.8 seconds. The schedule contains 22,464,000 uncompressed velocity bytes; the delivered container is approximately 2 MB before additional diagnostic logs.

The unchanged official Windows CUDA solver was exercised with zero flow, constant flow, stronger flow, reversed flow, outside-domain flow, acceleration comparison, spatial variation and a cloth entering/leaving a field. Zero and outside-domain cases matched the control; stronger and reversed flow produced the expected direction and magnitude changes. A 2 m/s field drove the synthetic cloth toward 1.9999 m/s.

The real FLIP-field test used source frames 150–154 and the original Plane topology (2,704 vertices, 5,202 triangles). The original Plane lay outside water support at that time. A separate fixture translated by `(2, 2, 0)` metres therefore tested immersion without modifying the user's scene. Over four simulation frames it moved an additional mean **0.1627 m along X**, with maximum displacement 0.3902 m. Control and flow runs each took about 8.4 seconds. This test deliberately omitted pins, collider and gravity; it does not certify the complete original-scene setup.

The full Python regression suite passed 2,062 tests, with 19 skips and three artifact tests deselected. The 17 Water Flow tests also passed after the final timing, upload-budget and array-read changes. A real Blender source registration/reload smoke test passed. Interactive GPU overlay appearance and a full Water Flow cancel/resume/Start Fresh scene run remain unverified.

## Scope

This is the first functional Water Flow implementation, with the limits above. Wetness, reactive materials, tearing, buoyancy and two-way coupling are excluded. Cache fingerprints cover source position/velocity contents, transforms, timing, requested frames and reconstruction settings. Debug display changes do not alter physics fingerprints. Disabled Water Flow leaves the existing payload path unchanged and does not read FLIP data.

Implementation: `cloth_next/gaia/`, `cloth_next/blender/water_flow.py`, and the existing parameter-encoding integration in `cloth_next/blender/solver_test.py`. Reproducible probes are in `tools/blender_convert_water_flow.py`, `tools/audit_gaia_water_fields.py` and `tools/audit_gaia_actual_water.py`. Delivered data and validation are `dist/GAIA_Watertest.gaia` and `dist/water-flow-validation.gaia`.
