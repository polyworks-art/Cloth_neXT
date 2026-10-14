# Water Push controlled prototype: stopped at safety gate

**Correction from the subsequent public-API audit:** object-statistics velocity is derived from consecutive output-frame center positions, multiplied by FPS (`statistics.rs`). It is not an independent instantaneous-velocity oracle. The table below contains output-interval average speed ratios. Its >1.30 values still prove overshoot, but do not measure the instantaneous peak. The subsequent cadence audit uses a separately validated force-impulse reference for drag-free uniform translation; its corresponding native peak is approximately 1.352. Historical JSON/CSV names containing `actual_object_velocity` are legacy labels for these statistics, not exact native state.

No production integration, new UI, default, or release. The mandatory speed-cap stop condition was reached before calibration and constrained-cloth testing. Official source velocity is unchanged.

## Control law

`relative = sampled_water_velocity − estimated_cloth_velocity`
`desired = relative × occupancy / response_time`
`applied = relative × min(occupancy / response_time, 1 / window_upper_bound)`

The prospective window bound is 1/24 s + the native maximum timestep (0.01 s), allowing the native solver to overshoot an output hold by one step. This limits the calculated velocity increment against the estimated velocity. It does not limit the increment against an unavailable instantaneous per-vertex velocity.

## Velocity source and measurements

Public `get.vertex(authoritative_frame)` output positions provide secant velocities over `(current_frame − previous_frame) / fps`. Skipped boundary intervals are handled, not assumed to be one frame. The first state is known stationary. Public object statistics also provide an output-interval secant, not an independent instantaneous reference, even for uniform translation.

## Spatial delivery and isolation

The exact previous nine-vertex 0.2 × 0.2 m patch geometry and density 1 / Baraff–Witkin / stretch 1000 / sideways response 0.4 / bend 0.83 / strain limit 4% are used. Three separate groups retain their original +X 1 m/s air-velocity grids and receive separate uniform acceleration grids. Uniformity is checked (velocity spread ≤0.001 m/s); heterogeneous velocity samples are rejected. This is an exact spatial representation for the uniform safety fixture only, not a claimed general per-vertex force implementation. No general reconstruction is promoted to production. Group targeting prevents one patch’s state driving another.

## Declared tolerance and results

Maximum projected speed ratio ≤1.01 (1% numerical overshoot), declared in the gate source before either run. Native dt=0.01, output fps=24, water speed=1 m/s, response times 1.0 / 0.1 / 0.05 s.

| Air density | Response s | Maximum speed ratio | Peak frame | Peak time s | Pass |
|---:|---:|---:|---:|---:|---|
| 0.001 | 1.0 | 0.757554 | 30 | 1.250000 | yes |
| 0.001 | 0.1 | 1.008025 | 6 | 0.250000 | yes |
| 0.001 | 0.05 | 1.300430 | 3 | 0.125000 | NO |
| 0.0 | 1.0 | 0.470503 | 15 | 0.625000 | yes |
| 0.0 | 0.1 | 1.007646 | 6 | 0.250000 | yes |
| 0.0 | 0.05 | 1.301444 | 3 | 0.125000 | NO |

The Water-Push-only trial keeps the original air-velocity field uploaded but disables its aerodynamic force solely in this isolated diagnostic (air-density=0). No production/global air-density workaround is used. Its overshoot reproduces the failure without the existing aerodynamic coupling.

## Failure mechanism and blocker

The secant velocity is the mean over the previous output interval. During rapid acceleration it lags current velocity. The bounded push is therefore computed from an overestimated remaining velocity error; the next constant spatial field continues accelerating after the actual target has already been reached. Both output-secant measurements show approximately 30% overshoot at response 0.05 s. The algebraic clamp passes unit tests but is not a robust physical speed limiter.

The official acceleration API evaluates `a(x,t)` and its script interface accepts x/y/z/time, not a vertex velocity. Object statistics cannot replace per-vertex state on deformed cloth. A lag-compensated estimator or a supported velocity-aware control interface would need a separate safety proof, particularly with pins, deformation, collisions and changing flow. This audit does not claim every possible controller is impossible; it establishes that this boundary-secant/clamped-acceleration prototype fails the mandatory requirement. No smaller gains or arbitrary constants are used to conceal that failure.

## Technical failures

The first combined diagnostic completed its native simulation, then its summary writer rejected the null initial statistic. Data were recovered from public native outputs; the writer now treats the known stationary first state correctly. The isolated trial hit a Windows sharing violation while replacing the public hold marker after 16 recorded output frames; the owned native process was checkpointed and shut down. Overshoot had already occurred before that transport failure. The gate now retries this public operation on a bounded schedule. No solver code was patched.

Debug CSV/JSON include positions, estimated and actual velocities, local water, relative velocity, desired/applied acceleration and occupancy. Control-law entries are explicitly labeled retrospective reconstructions from the actual outputs, rather than a claimed retained live trace; terminal samples do not claim a subsequent applied field.

## Validation status

57 targeted tests passed, covering the prototype algebra, skipped boundary velocity estimates, dry support, matched-velocity zero push, existing held stream, Water Flow streaming/window and Blender descriptors. This is not a full product regression run. The old bake path and normal UI remain unchanged; the prototype module has no production imports.

Not run after the speed-cap stop: full 0.5/1/2/4 m/s × response-time calibration; dt=0.001/0.002 comparison; constrained patch; changing direction; enter/exit native tests; unrelated-Wind numerical comparison; general heterogeneous spatial reconstruction. Dry/matched behavior is algebraically tested only. Response-time calibration, timestep independence, constrained response, and transition stability are therefore NOT established.

## External solver integrity

All 14,606 packaged files were compared byte-for-byte with the original official 2026-09-27-20-44 Windows ZIP: zero differences. Archive SHA-256: `fd3f43aa7e9849526b5113da558443a074039231ba700edfc953b6c83eb990d4`. Ordinary runtime sessions are additional audit data, not patched package files. See `solver-integrity.json`.

No production default or release is selected. The correct next step is resolving velocity estimation and speed limiting before any artist-facing Water Push feature.
