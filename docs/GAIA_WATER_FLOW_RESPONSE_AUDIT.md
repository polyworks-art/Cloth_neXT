# GAIA Water Flow response diagnosis

Read-only analysis of `F:\GAIA_Watertest.blend`, its current baked PC2, and `F:\16_Cloth_neXt\cache_AD\Plane_WaterFlow.gaia`. Official GAIA 0.23 CUDA remains unchanged. No production parameters or force model were changed.

## Decision: C — force-field-weight is not enough

The existing path correctly accelerates a free patch toward its local flow. The production pinned sheet scarcely follows the water. Increasing the official field weight changes the target air velocity, so it overshoots the physical source velocity rather than independently strengthening coupling. A separate Water Push / acceleration component is a candidate for a later task, with response-time and speed-cap validation; it is not implemented here. Do not tune a production default from this audit.

## Production measurement

Water is sampled by trilinear interpolation of the prepared occupancy-weighted grid at each actual evaluated world-space cloth position, every source frame 1–250. Cloth velocity and acceleration use centered output-frame differences (24 fps); endpoints use one-sided differences. No initial-position-only sampling. The below means use valid occupied frames with water speed >0.05 m/s.

| Point | Vertex | Wet frames | Mean water m/s | Mean cloth m/s | Mean ratio | Mean alignment | Minimum relative m/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| wet_free | 520 | 236 | 5.3433 | 0.027801 | 0.0052469 | -0.0026428 | 4.5266 |
| wet_edge | 197 | 236 | 5.3423 | 0.027322 | 0.005156 | 0.051204 | 4.4267 |
| pinned | 1 | 0 | — | — | — | — | — |

The interior and edge velocities oscillate about zero; their mean alignment therefore approaches zero. They are part of a sheet coupled to 52 soft-pinned vertices and stiff neighboring faces. Negative instantaneous acceleration alignment is not proof of reversed Water Flow: internal forces and gravity also act. The pinned control is above the liquid, so it is not a wet-pin response test.

## Representative per-frame table

Full XYZ positions, water/cloth/relative velocity vectors, acceleration vectors, both alignments, occupancy and world normals are in `real-wet_free.csv`, `real-wet_edge.csv`, `real-pinned.csv`. Compact interior sample:

| Frame | Time s | Position XYZ m | Water XYZ m/s | Cloth XYZ m/s | Relative m/s | Acceleration XYZ m/s² | Alignment | Ratio | Occupancy |
|---:|---:|---|---|---|---:|---|---:|---:|---:|
| 50 | 2.042 | -0.9581, -0.3334, 0.2603 | 5.3027, -0.0317, 0.0105 | -0.0368, 0.0000, -0.0009 | 5.3396 | -0.1176, -0.0002, -0.0036 | -0.9997 | 0.00694 | 1.000 |
| 100 | 4.125 | -0.9586, -0.3334, 0.2603 | 5.4588, -0.0421, -0.0219 | 0.0109, -0.0001, 0.0004 | 5.4481 | -0.1262, 0.0008, -0.0042 | 0.9993 | 0.00201 | 1.000 |
| 150 | 6.208 | -0.9683, -0.3334, 0.2601 | 5.2992, -0.0178, -0.1034 | 0.0342, -0.0001, 0.0007 | 5.2661 | -0.1145, 0.0001, -0.0030 | 0.9992 | 0.00645 | 1.000 |
| 200 | 8.292 | -0.9798, -0.3333, 0.2598 | 5.4736, 0.0299, -0.1315 | 0.0342, -0.0000, 0.0004 | 5.4411 | -0.0212, -0.0004, 0.0001 | 0.9993 | 0.00624 | 1.000 |
| 250 | 10.375 | -0.9863, -0.3334, 0.2598 | 5.4568, -0.0216, -0.1126 | 0.0078, -0.0000, 0.0000 | 5.4503 | 0.0254, -0.0001, -0.0001 | 0.9997 | 0.00143 | 1.000 |

## FLIP reconstruction cross-check

Frames 150–155, interior vertex: nearest particle distance 0.0120–0.0327 m; mean vector error between raw particle velocity and field at its position 0.4504 m/s. Raw source velocity is never rescaled. See `particle-diagnostics.json` for the local neighborhood and all selected vertices.

The supplied `flip_id` values repeat within a single frame; unique UID files are absent. Consequently no single-particle trajectory is presented as uniquely tracked. Nearest same-frame samples and neighborhoods are the secondary diagnostic. The hidden FLIP cache has a stale identity matrix in background Blender; the diagnostic resolves its authored parent/inverse/basis matrix using FLIP’s existing transform. It does not alter FLIP.

## Controlled response

Nine-vertex 0.2 × 0.2 m patches, no pins, gravity, contact or colliders; all source fields are uniform physical +X velocities, occupancy 1, with broad bounds that contain the entire trajectory. Independent groups run together; no contacts couple patches. Measurements use each patch’s center vertex. Identical structural material parameters across cases.

| Water m/s | Weight | Surface density | Normal | Air density | dt s | Final cloth m/s | Ratio | t25 s | t50 s | t75 s | t90 s | Fitted tau s | Min relative m/s | Peak acceleration m/s² | Alignment |
|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 1.0 | 1.0 | X | 0.001 | 0.001 | 0.4702 | 0.9403 | 0.2917 | 0.7083 | 1.417 | 2.333 | 1 | 0.02978 | 0.4919 | 1 |
| 1.0 | 1.0 | 1.0 | X | 0.001 | 0.001 | 0.9403 | 0.9403 | 0.2917 | 0.7083 | 1.417 | 2.333 | 1 | 0.05956 | 0.9839 | 1 |
| 2.0 | 1.0 | 1.0 | X | 0.001 | 0.001 | 1.881 | 0.9406 | 0.2917 | 0.7083 | 1.417 | 2.333 | 1 | 0.1189 | 1.968 | 1 |
| 4.0 | 1.0 | 1.0 | X | 0.001 | 0.001 | 3.761 | 0.9403 | 0.2917 | 0.7083 | 1.417 | 2.333 | 1 | 0.2382 | 3.935 | 1 |
| 1.0 | 2.0 | 1.0 | X | 0.001 | 0.001 | 1.881 | 1.881 | 0.1667 | 0.2917 | 0.5 | 0.625 | 1 | 0.01495 | 1.968 | 1 |
| 1.0 | 5.0 | 1.0 | X | 0.001 | 0.001 | 4.574 | 4.574 | 0.08333 | 0.125 | 0.1667 | 0.2083 | 1 | 0.05925 | 4.919 | 1 |
| 1.0 | 10.0 | 1.0 | X | 0.001 | 0.001 | 9.148 | 9.148 | 0.04167 | 0.08333 | 0.08333 | 0.125 | 1 | 0.1763 | 9.839 | 1 |
| 1.0 | 25.0 | 1.0 | X | 0.001 | 0.001 | 23.1 | 23.1 | 0.04167 | 0.04167 | 0.04167 | 0.04167 | 1 | 0.02485 | 24.6 | 1 |
| 1.0 | 50.0 | 1.0 | X | 0.001 | 0.001 | 46.3 | 46.3 | 0.04167 | 0.04167 | 0.04167 | 0.04167 | 1 | 1.05 | 49.19 | 1 |
| 1.0 | 100.0 | 1.0 | X | 0.001 | 0.001 | 92.43 | 92.42 | 0.04167 | 0.04167 | 0.04167 | 0.04167 | 1 | 3.099 | 98.39 | 1 |
| 1.0 | 1.0 | 0.1 | X | 0.001 | 0.001 | 0.9973 | 0.9973 | 0.04167 | 0.08333 | 0.1667 | 0.25 | 0.1034 | 0.002693 | 7.768 | 1 |
| 1.0 | 1.0 | 10.0 | X | 0.001 | 0.001 | 0.2623 | 0.2623 | 2.833 | — | — | — | 9.953 | 0.7377 | 0.1009 | 1 |
| 1.0 | 1.0 | 1.0 | Z | 0.001 | 0.001 | 0.4477 | 0.4477 | 1.458 | — | — | — | 5.013 | 0.5523 | 0.2011 | 1 |
| 1.0 | 1.0 | 1.0 | X | 0.01 | 0.001 | 0.9971 | 0.9971 | 0.04167 | 0.08333 | 0.1667 | 0.25 | 0.1034 | 0.002755 | 7.769 | 1 |
| 1.0 | 1.0 | 1.0 | X | 0.001 | 0.002 | 0.7743 | 0.7743 | 0.5833 | 1.417 | 2.792 | — | 2 | 0.2257 | 0.5047 | 1 |
| 1.0 | 1.0 | 1.0 | X | 0.001 | 0.01 | 0.2579 | 0.2579 | 2.875 | — | — | — | 9.977 | 0.7421 | 0.1119 | 1 |

The JSON records 10/25/50/75/90% and 63% thresholds in seconds and frames. Thresholds for weights >1 refer to the original physical water speed; reaching them once does not mean following that speed thereafter. Response CSVs also record the effective weighted target. Three-second observation; a dash means not reached, not impossible.

## Parameters and semantics

Production material metadata: surface density 1, Baraff–Witkin, stretch 1000, sideways response 0.4, bend 0.83, stretch limit 4%, shape/fold damping 0, soft pin strength 100, 52 pins, 24 fps, gravity −Z 9.81. Water influence and velocity scale are both 1.

Air-density and air-friction are absent from the persisted production Param payload: official defaults are 0.001 and 0.2; isotropic-air-friction defaults to 0. These are distinct from the unused object Force UI defaults (0.01 and 0.2). Weight for Water Flow is explicitly 1. The saved blend currently has Water Flow off, but completed recovery telemetry proves all 249 windows were uploaded.

Provenance caveat: PC2 metadata reports dt 0.001 and CG tolerance 0.001, whereas the current blend and most recent cached base Param report dt 0.01 and CG tolerance 0.01. The finished native project was removed by normal cleanup. The actual native dt cannot be re-read directly; do not treat the current settings as an exact record of the completed bake. The controlled fixtures record their actual native `param.toml` and both drag parameters explicitly.

Official `external_field.kernel.cpp` multiplies the air-velocity sample by field weight. `momentum.kernel.cpp` adds that result to wind without gust modulation. `air_damper.hpp` then applies normal response plus air-friction × tangential response, scaled by vertex area × air-density. Thus at friction 0.2 a parallel surface has about one fifth of the directional response coefficient. Both drag and inertial energies divide by dt²: this is a discrete velocity relaxation, not a calibrated water-density drag law. Its response time depends on integration timestep; this must be accounted for in later coupling design.

The real wet face has |normal·flow direction| close to 1: orientation attenuation is not its main issue. Controlled mass and tangential cases verify the expected sensitivity. Uniform source speeds 0.5/1/2/4 m/s produce nearly identical normalized response and acceleration proportional to speed. High field weights retain the same relaxation coefficient while changing the asymptotic target; they are unsuitable as a physical coupling-strength control.

## Artifacts

All measurements, plots and this report are bundled into `GAIA_Water_Response_Audit.gaia`. The original blend, PC2, water container and external solver remain unchanged.
