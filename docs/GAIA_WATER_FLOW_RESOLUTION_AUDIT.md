# GAIA Water Flow resolution audit

Unpublished working-tree change after 2.9.9. No release or external solver modification. The supplied scene and FLIP bake remain unchanged.

This records the completed spatial phase before production streaming. Its whole-schedule limitations are historical; the subsequent [streaming audit](GAIA_WATER_FLOW_STREAMING_AUDIT.md) verifies bounded production at 100/150/200 and the real 250-frame sequence without changing this reconstruction.

## Spatial sizing

The existing F:/Plane_WaterFlow.gaia contains 24 x 24 x 13 simulation nodes and 250 frames. The previous display stride of four explains roughly 6 x 6 visible horizontal samples. A separate 6 x 6 x 4 reconstruction is also benchmarked as a coarse reference; it was not the existing file's physical grid.

Auto now has a longest-axis floor of 100 and incorporates reliable FLIP cached dx after its existing world transform. Medium/High/Extreme request 100/150/200; Custom exposes one longest-axis target. Older numeric identifiers resolve to the new floor. Saved Blender enum values for Auto, Medium and High are retained; obsolete Low defaults to Auto. Auto rejects excessive source-derived grids rather than lowering detail silently.

One target voxel size L/N derives all counts using ceil(extent/voxel), with a minimum of two nodes required by GAIA's existing trilinear interface. The grid preserves the inclusive boundary-node contract, so actual node spacing is extent/(count-1); it is recorded separately from nominal voxel size. For the supplied scene Auto is 100 x 100 x 50, nominal voxel 0.08 m, actual node spacing (0.0808081, 0.0808081, 0.0816327) m.

Bounds are (-4,-4,0) to (4,4,4) m. They cover the existing domain/cache union. The source occupied bounds at frame 150 are approximately (-3.87828,-3.87937,0.120537) to (3.87172,3.87192,1.02). The provider has no verified occupied union across the requested animation. Cropping to one frame's particles would lose later flow, so this change intentionally keeps the safe bounds; no provider redesign or extra full-animation prepass was added.

Reconstruction remains normalized trilinear particle splatting. Its support threshold scales with physical node-volume relative to the 100-node reference (1.0, 0.292322, 0.122497 for 100/150/200). Without this correction, refinement lowers particle mass per node and spuriously weakens bulk water contribution. Empty nodes remain zero; no extrapolation or hole filling was introduced. Velocity units and the official force-field adapter are unchanged.

## Frame-150 performance

579,789 verified liquid particles, same bounds and source velocities in isolated Python processes. Cache sizes below are compressed velocity/support data plus metadata per frame. Estimated working memory includes velocity/support, float64 splat buffers, temporary particle/chunk arrays and encoded-field staging, excluding interpreter/Blender base memory. Peak working set is OS-measured process RAM including interpreter overhead.

| Longest | Dimensions XYZ | Cells | Nominal voxel m | Convert s/frame | Cache MiB/frame | Estimate MiB | Peak process MiB |
|---|---|---:|---:|---:|---:|---:|---:|
| 6 | 6 x 6 x 4 | 144 | 1.333333 | 0.349 | 0.0018 | 71.8 | 119.8 |
| 24 | 24 x 24 x 13 | 7,488 | 0.333333 | 0.345 | 0.0200 | 72.5 | 119.9 |
| 100 | 100 x 100 x 50 | 500,000 | 0.080000 | 0.374 | 0.6071 | 117.6 | 147.3 |
| 150 | 150 x 150 x 75 | 1,687,500 | 0.053333 | 0.451 | 1.6442 | 226.3 | 219.7 |
| 200 | 200 x 200 x 100 | 4,000,000 | 0.040000 | 0.571 | 3.3967 | 438.0 | 362.2 |

## Fixed world-space probes

Contributions below are the X component after support weighting, in m/s. Full three-component raw velocity and contribution values, positions, occupancy counts and neighboring-node differences are stored in the audit container.

| Probe | World XYZ m | 6 | 24 | 100 | 150 | 200 |
|---|---|---:|---:|---:|---:|---:|
| fast_interior | [3.3, -3.1, 0.2] | 4.796161 | 4.890744 | 5.188986 | 5.241917 | 5.285626 |
| fast_low | [3.3, -3.1, 0.3] | 4.801187 | 4.922285 | 4.250295 | 2.635064 | 1.962389 |
| fast_mid | [3.3, -3.1, 0.55] | 4.813752 | 3.420039 | 0.000000 | 0.000000 | 0.000000 |
| fast_high | [3.3, -3.1, 0.8] | 4.826317 | 1.563285 | 0.000000 | 0.000000 | 0.000000 |
| interior | [0.0, 0.0, 0.25] | 4.581953 | 4.727125 | 5.396262 | 5.532550 | 5.550929 |
| above_water | [3.3, -3.1, 1.3] | 4.851446 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| dry_in_domain | [0.0, 0.0, 2.0] | 2.348664 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |
| outside | [5.0, 0.0, 0.5] | 0.000000 | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

Bulk fast-flow X converges from 5.189 to 5.242 to 5.286 m/s. A near-surface probe at Z=0.3 changes more (4.250/2.635/1.962): finer grids sharpen the occupancy boundary. This is not a claim that every boundary sample has already converged. Dry probes above the liquid are zero at the new resolutions; the coarse 6-node grid spreads liquid contribution up to Z=2 m.

## Unchanged official CUDA solver

Three disconnected 9-vertex patches of one cloth: a wet patch centered at (3.3,-3.1,0.2), a dry patch inside domain bounds at Z=1.3, and a patch outside at X=5. Identical rest shape, zero gravity, no pins/collider, air-density 0.1, air-friction 1, dt 0.001, four simulation frames at 60 FPS. The real FLIP source is held constant at frame 150 to isolate spatial resolution. This validates numerical trajectories, not a complete 250-frame original-scene production Bake.

| Resolution | Wet mean X delta m | Dry in-domain max delta m | Outside max delta m | Field export s | Reload + first frame s | Four-frame Bake s |
|---|---:|---:|---:|---:|---:|---:|
| 6 | 0.271943897 | 0.275104761 | 0.000000000 | 0.0030 | 1.4722 | 5.7872 |
| 24 | 0.277093291 | 0.000000000 | 0.000000000 | 0.0026 | 1.4732 | 5.6839 |
| 100 | 0.288916141 | 0.000000000 | 0.000000000 | 0.0040 | 1.4724 | 6.0422 |
| 150 | 0.291924238 | 0.000000000 | 0.000000000 | 0.0102 | 1.4742 | 5.8398 |
| 200 | 0.293573648 | 0.000000000 | 0.000000000 | 0.0406 | 1.4726 | 5.6859 |

100 differs from 200 by 1.59% in wet mean X displacement. The coarse 6-node field falsely displaces the dry patch by 0.275105 m; the new resolutions leave it unchanged.

Field-export time measures the public held frontend update writing its official field representation. Reload plus first frame includes GPU loading and simulation; an isolated GPU-upload time cannot be obtained from this interface and is not fabricated. Bake timing includes frontend polling overhead and is a small-fixture measurement, not a production throughput benchmark. All three official frontend files used (_session_, _force_field_, _scene_) were byte-compared with the pinned official archive after testing and remain identical.

## Memory, cache and E102

The working-memory safety gate is 512 MiB and a derived frame is limited to 128 MiB. Errors include requested resolution, dimensions and estimated MiB before grid allocation. The generic .gaia envelope remains schema 1: dimensions were already fingerprinted; adding isotropic-longest-axis-v2 layout, source voxel size, normalized support threshold and trilinear-splat-support-v2 invalidates legacy coarse caches without breaking unrelated containers. Custom resolution participates in settings freshness checks. Older FLIP previews lacking the new grid recipe are suppressed until preparation.

The reported E102 was actually the 32 MiB upload gate, not an invalid frame range. Standalone Prepare now performs frame-wise conversion to disk independently of total schedule size. Production keeps its existing 32 MiB uncompressed velocity budget and reports CNX-E147 for Water Flow memory limits. Source hashing, bounded queues, atomic writes and cancellation remain intact. Writer and generator explicitly release each field before requesting the next; a weak-reference regression verifies this ordering. An eight-frame repeated-source memory check wrote 4.85 MiB in 3.514 s with 148.8 MiB peak RAM, approximately the same as a single frame. The eight-frame check intentionally repeats source frame 150 solely to measure memory growth.

The immutable production upload cap has important consequences with these safe full-domain bounds:

| Resolution | Uncompressed velocity MiB/frame | Maximum frames in one 32 MiB schedule |
|---|---:|---:|
| 100 | 5.72 | 5 |
| 150 | 19.31 | 1 |
| 200 | 45.78 | 0 |

Extreme 200 cannot fit even one frame in the existing production schedule. Its physical benchmark uses the already-existing public held-update frontend, only as a diagnostic; production streaming was not added. Longer high-resolution production Bakes require a separate pipeline/budget change or verified occupied bounds across the animation. Preparation of their .gaia caches no longer fails solely because of the upload cap.

## Display, diagnostics and regression

The Flow Display debug area shows current source frame, dimensions, nominal voxel size, bounds, occupied/total nodes and estimated memory. Its adaptive display stride targets about 20 samples along the longest axis. Each display bucket selects an occupied representative; a regular slice could miss the entire thin liquid layer. GPU buffers remain contiguous float32 and never alter the simulation field. Four real GPU captures at resolution 100 pass projected-endpoint/raster extent checks and were inspected visually.

Real Blender Auto preparation, verified cache reuse and cancellation pass for source frames 1–3 and restore the timeline. Real Blender RNA/UI registration passes all twelve controls including Custom. Full regressions: 2092 passed, 19 skipped, 3 deselected; final targeted regressions: 140 passed. Skips include unconfigured automated external integrations; the manual official CUDA comparisons above were actually run. Source structure, icon validation, compilation and diff whitespace checks pass.

Reproducible tools: tools/audit_gaia_water_resolution.py, tools/audit_gaia_water_resolution_solver.py, tools/blender_water_preparation_smoke.py and tools/blender_water_overlay_capture.py. Derived fields, numerical/trajectory results, performance records and GPU captures are bundled in dist/GAIA_Water_Resolution_Audit.gaia. No release was published.
