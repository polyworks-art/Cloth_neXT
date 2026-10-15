# GAIA Water Flow — first implementation

Water Flow reconstructs a time-dependent velocity grid from baked FLIP liquid particles and their `flip_velocity` attribute. It submits the result through GAIA 0.23's existing `air-velocity` force-field interface. Neither GAIA nor FLIP source is modified or bundled.

## Artist workflow

Enable GAIA Water Flow on an individual Cloth object, select its baked FLIP Domain, choose Influence, Velocity Scale, grid resolution and GAIA Path, then prepare the cache and run the existing GAIA workflow. Optional debug vectors show the reconstructed contribution. Velocity export must be present in the requested bake frames. Current Cloth FPS must match baked FLIP FPS; animated FPS and explicit retiming are rejected.

GAIA Path accepts a folder or a new file name and creates a `.gaia` container. An empty path uses the normal Cloth cache folder. Its tooltip is "Where should the File be placed?". Preparation uses the existing Bake Window with its two progress bars and Cancel button; Details is hidden. Hashing and reconstruction run in a Blender-free worker, while a timer captures one FLIP frame at a time on the main thread. Production Bake performs the same preparation before continuing on the same job, reusing the verified result. Completion, cancellation and shutdown restore the original scene frame.

Influence scales the reconstructed velocity contribution; Velocity Scale changes its magnitude. GAIA's existing scene air density and air friction govern the resulting relative drag. There is no independent water-only drag coefficient in this implementation. This is an approximation using the official relative air-velocity model, not CFD water pressure, buoyancy or quadratic hydrodynamic drag.

## Spatial resolution (unpublished after 2.9.9)

Auto uses at least 100 samples on the longest axis and follows reliable FLIP source voxel detail. Medium/High/Extreme use 100/150/200; Custom chooses one longest-axis target. Counts on other axes derive from the same physical voxel size. Field memory is checked before allocation. The Flow Display diagnostics show dimensions, voxel size, bounds, occupied nodes and estimated RAM, while its adaptive occupied-bucket sampling keeps vectors readable.

Preparation writes frames to disk independently of the old schedule upload budget. Production Water Flow now streams current and next source frames through official GAIA 0.23 held updates. The existing 32 MiB whole-schedule limit is unchanged; the separate active streaming values budget is 128 MiB across all targets and preserved base grids. Resolution is never silently reduced. The new grid recipe invalidates older coarse caches. See the [streaming audit](GAIA_WATER_FLOW_STREAMING_AUDIT.md) for transport, memory and lifecycle measurements.

## Field and container

The FLIP adapter reads all liquid particle categories regardless of viewport sampling percentages, restoring its temporary import settings afterward. Positions are transformed into Blender world space; physical world velocities are not transformed twice. A normalized trilinear splat reconstructs velocity and local support. Empty support and positions outside the field contribute zero additional flow. The surface-velocity fallback has near-surface support only and is not a volumetric reconstruction.

The generic adapter rotates world coordinates into GAIA's existing convention and writes its official grid schema. The solver samples the field at current simulated positions, interpolating space and time. No animated rest-position or post-simulation displacement mechanism is used.

`.gaia` is a versioned ZIP container holding the manifest, frame velocity/support arrays, checksums and source fingerprints. Writes replace atomically; cancellation preserves the previous container. Reads enforce size limits and reject corrupt arrays, duplicate members and invalid times. Diagnostic and validation records are bundled in the delivered containers. Native GAIA runtime staging still requires its official temporary files.

Production reads validated, frame-addressable samples from the prepared container. It replaces the official Force Field while held, releases temporary arrays and advances to the next output boundary; the native process and integration state persist. Source frame `Cloth start + actual held boundary` maps to simulation time `boundary / FPS`. Current/next samples preserve temporal interpolation. Recovery authenticates the checkpoint, cache identity and scene parameters before resuming the corresponding window. Start Fresh resets simulation state and retains reusable environmental caches. Shared material groups are rejected to avoid applying an object's flow to other objects.

## Historical verification before the coordinate audit

These initial results precede the corrected coordinate evaluation in 2.9.9 and do not certify the current field layout. See [coordinate audit](GAIA_WATER_FLOW_COORDINATE_AUDIT.md) and [resolution audit](GAIA_WATER_FLOW_RESOLUTION_AUDIT.md) for current validation.

Source: `F:\GAIA_Watertest.blend` and its adjacent FLIP cache. Frames 1–250 at 24 FPS were converted at resolution 24, using 66,727 liquid samples in the first frame and 589,284 in the last. Conversion took 81.8 seconds. The schedule contains 22,464,000 uncompressed velocity bytes; the delivered container is approximately 2 MB before additional diagnostic logs.

The unchanged official Windows CUDA solver was exercised with zero flow, constant flow, stronger flow, reversed flow, outside-domain flow, acceleration comparison, spatial variation and a cloth entering/leaving a field. Zero and outside-domain cases matched the control; stronger and reversed flow produced the expected direction and magnitude changes. A 2 m/s field drove the synthetic cloth toward 1.9999 m/s.

The real FLIP-field test used source frames 150–154 and the original Plane topology (2,704 vertices, 5,202 triangles). The original Plane lay outside water support at that time. A separate fixture translated by `(2, 2, 0)` metres therefore tested immersion without modifying the user's scene. Over four simulation frames it moved an additional mean **0.1627 m along X**, with maximum displacement 0.3902 m. Control and flow runs each took about 8.4 seconds. This test deliberately omitted pins, collider and gravity; it does not certify the complete original-scene setup.

The preparation regression suite passed 2,074 tests, with seven skips and 15 integration/artifact tests deselected. A real Blender preparation test with source frames 1–3 verified cache creation, reuse and cancellation, with a maximum main-thread pump duration of approximately 80 ms. The actual preparation window was captured and verified to hide Details. The 17 Water Flow tests also passed after the final timing, upload-budget and array-read changes. A real Blender source registration/reload smoke test passed. Interactive GPU overlay appearance and a full Water Flow cancel/resume/Start Fresh scene run remain unverified.

## Scope

This is the first functional Water Flow implementation, with the limits above. Wetness, reactive materials, tearing, buoyancy and two-way coupling are excluded. Cache fingerprints cover source position/velocity contents, transforms, timing, requested frames and reconstruction settings. Debug display changes do not alter physics fingerprints. Disabled Water Flow leaves the existing payload path unchanged and does not read FLIP data.

Implementation: `cloth_next/gaia/`, `cloth_next/blender/water_flow.py`, and the existing parameter-encoding integration in `cloth_next/blender/solver_test.py`. Reproducible probes are in `tools/blender_convert_water_flow.py`, `tools/audit_gaia_water_fields.py` and `tools/audit_gaia_actual_water.py`. Delivered data and validation are `dist/GAIA_Watertest.gaia` and `dist/water-flow-validation.gaia`.
