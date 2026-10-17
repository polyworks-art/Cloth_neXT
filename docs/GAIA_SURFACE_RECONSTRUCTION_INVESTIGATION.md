# GAIA surface reconstruction investigation — NO-GO

Read-only source audit completed. No reconstructed SDF was accepted or connected to the solver. The requested new short bake and 130–159 bake were not run because physical equivalence is unproven. The previous successful 131–133 CUDA bake and all CPU/CUDA artifacts are preserved.

## Frames 130–160

|Frame|Closed components|Open edges|Non-manifold edges|Zero-area triangles|Simple boundary loops|
|---|---:|---:|---:|---:|---:|
|130|404|7|1|0|0|
|131|378|0|0|0|0|
|132|403|0|0|0|0|
|133|429|0|0|0|0|
|134|394|0|0|0|0|
|135|362|2|2|0|0|
|136|354|0|0|0|0|
|137|367|0|0|0|0|
|138|351|0|0|0|0|
|139|353|0|0|0|0|
|140|394|3|1|0|0|
|141|405|0|0|0|0|
|142|386|0|0|0|0|
|143|371|0|0|0|0|
|144|367|0|0|0|0|
|145|387|0|0|0|0|
|146|364|3|1|0|0|
|147|354|0|0|0|0|
|148|346|0|0|0|0|
|149|341|7|1|0|1|
|150|334|6|4|0|0|
|151|310|6|0|0|0|
|152|281|0|0|0|0|
|153|303|0|0|0|0|
|154|298|0|0|0|0|
|155|286|0|0|0|0|
|156|342|3|1|0|0|
|157|362|0|0|4|0|
|158|367|2|2|0|0|
|159|379|3|1|0|0|
|160|330|3|1|0|0|

Disconnected closed components are valid. Zero simple boundary loops does not imply closure: branched or interrupted boundary graphs are recorded separately in the JSON. Closed-component counts are edge-topology counts, not proof of absence of self-intersections.

## Root-cause evidence

Frame 130: 7 open and 1 non-manifold edges; problem regions: `[{"minimum": [0.45543622970581055, -0.659839391708374, 0.4710272550582886], "maximum": [0.4640989303588867, -0.6559948921203613, 0.48159849643707275], "domain_distance_m": 0.4710272550582886, "boundary_edges": 7, "boundary_loop": false, "boundary_branch_vertices": 1, "boundary_endpoints": 1}]`. These are internal topological defects, not ordinary disconnected water bodies or evidence permitting domain caps. The exact upstream mesher defect cannot be established from the exported mesh alone.
Frame 135: 2 open and 2 non-manifold edges; problem regions: `[{"minimum": [0.4224332571029663, 0.7043895721435547, 0.595291018486023], "maximum": [0.42932963371276855, 0.7068729400634766, 0.6036452054977417], "domain_distance_m": 0.5742194652557373, "boundary_edges": 2, "boundary_loop": false, "boundary_branch_vertices": 0, "boundary_endpoints": 2}]`. These are internal topological defects, not ordinary disconnected water bodies or evidence permitting domain caps. The exact upstream mesher defect cannot be established from the exported mesh alone.

## Reconstruction decision

All 31 requested samples have structurally valid matching FFP3 position/velocity layouts. The cache inventory and all seven savestates expose particles, but no saved liquid SDF or MAC grid. The existing GAIA velocity/support field is not a liquid level set. The installed FLIP wrapper exposes surface output and simulation update, but no standalone cached liquid-SDF export.

A diagnostic particle-union candidate uses all ~8.78 million fluid particles and the original meshing radius `dx*(3/(32*pi))^(1/3)*3 = 0.014914832 m`; no radius fitting, hole filling, component deletion or domain capping. Original settings: dx 0.016028393 m, surface subdivisions 1 (wrapper adds 1), smoothing 0.5 / 2 iterations, obstacle meshing offset enabled. The source mesher also consumes a solid SDF. The diagnostic has not reproduced those solid boundaries, smoothing or polygonization.

The candidate is evaluated analytically with nearest-particle queries, without a reconstruction grid. On 20,000 deterministic vertex samples per reference frame 131–133 its scalar residual RMS is 5.28–5.43 mm, p95 absolute 10.90–10.99 mm, maximum absolute 13.77–14.22 mm. This is a residual of the sphere-union implicit field, not a symmetric Hausdorff surface error or a true interior SDF distance. The negative mean (−3.19 to −3.32 mm) reveals a directional occupancy bias at reference surface samples. Nearest-particle velocities are documented, but are not accepted as the transport velocity estimator.

**NO-GO:** there is no validated reconstructed surface, wet-cloth-area/force equivalence, independent occupancy acceptance or resolution convergence. No cell size is approved; a future grid could lose subcell droplets, gaps and sheets. These quantities cannot be reported as passed. No new force or trajectory result is fabricated. The existing SDF bounds guard, Cn=1/Ct=0, one-way coupling, materials and original sources are unchanged.

## Other intervals and preserved regression evidence

26 topologically valid 30-output-frame intervals with real next-frame lookahead were found; first: `{'first': 1, 'last_output': 30, 'lookahead': 31}`. These are separate possible tests; none replaces 130–159.

The previous 131–133 reference remains the only completed middle-sequence Blender test: 98.868/106.324 N sampled normal loads, 0.64358/0.62193 m² wet area, main-cloth hydro/dry max 56.248 mm, native/PC2 max 6.102e-8 m, exact evaluated playback after reload, zero audited collider crossings. 357 prior add-on tests passed. Previous native CPU/CUDA regressions remain unchanged; the strict state-dependent force comparison remains 19/20, not silently accepted. No native code or binaries changed during this investigation; no new native regression is claimed.

Release blockers: authoritative/reproducible particle-to-surface treatment including obstacle boundaries; independent occupancy, wet-area and force validation; resolution sensitivity; then new short bake and full 130–159 +160 end-to-end validation. No source mutation, version change, commit, push or release.

Upstream implementation references: [particle mesher](https://github.com/rlguy/Blender-FLIP-Fluids/blob/master/src/engine/particlemesher.cpp), [fluid simulation](https://github.com/rlguy/Blender-FLIP-Fluids/blob/master/src/engine/fluidsimulation.cpp). These explain the candidate only; current upstream is not asserted byte-identical to the cached simulation build.
