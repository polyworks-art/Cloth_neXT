# Water Push spatial architecture audit

**Classification C for general deforming/self-overlapping cloth. Stop development on the present spatial, object-group-targeted acceleration architecture.**

This is narrower than saying Water Push has no useful effect: nonoverlapping point targets can be fitted and separate objects can be isolated. The required same-object overlap case is significant and fails a representability gate that grid resolution cannot repair. No production code, UI, default, source water, normal Water Flow or external GAIA file was changed.

## Frozen experiment and thresholds

48 Hz, tau .10 s, existing nonuniform quadratic/BDF2 estimator, alpha=1−exp(−ΔT/tau), desired Δv=alpha×occupancy×(water−estimated cloth), acceleration=Δv/ΔT. Only the historical baseline replay uses its original 24-Hz timing to reproduce that recorded error; it is not a new working point.

Acceptance thresholds written before test execution: mean relative error ≤5%, P95 ≤10%, maximum ≤20%, maximum direction error ≤5° for targets ≥.1 m/s². Relative statistics exclude smaller targets; their absolute errors remain recorded. Dry diagnostic vertices require ≤1e−6 m/s². No threshold was relaxed. Constant inputs must be preserved within floating-point tolerance.

## Current method and the 1.68 m/s² root cause

The preceding prototype constructs a 5×13×13 grid over cloth bounds ±.1 m. Each grid node receives an inverse-squared-distance average of the four nearest cloth vertices. The same grid is then trilinearly sampled back at the vertices. This is two interpolations, not interpolation constraints satisfied at each vertex. Grid nodes mix different correction velocities; fixed coarse spacing and padding smooth gradients. Occupancy is only present in the target samples, not an independent off-vertex wet/dry gate.

Historical replay of the actual saved 81-vertex gravity/soft-pin trajectory reproduces maximum error **1.678642935862 m/s²**, exactly matching the recorded 1.678642935862. The cause is reconstruction/double sampling, not native integration error: this replay does not advance a solver.

## Synthetic reference fields and target data

Four analytic Blender-world fields: step +X .5/2.0 m/s; smooth +X .5→2.0; direction gradient +X→+Z; center strip +X2 with zero occupancy and zero water outside. Source fields are analytic and unchanged across Push resolutions. They do not inherit WaterVelocityField resolution.

Primary patch: 17×17, 289 material points over a 2 m square. Three manufactured position-history states represent a free translating/deforming patch, a top-edge constrained shape and a gravity-like constrained shape. They are host geometry/velocity states, not new native pinned/gravity simulations. BDF2 uses the actual three sample times. Existing production-like material constants are retained as reference metadata; host reconstruction itself does not simulate that material.

For all 12 states/fields the target dataset is persisted before reconstruction. desired-vertices.npz records position, estimated velocity, water, occupancy, relative velocity, desired Δv and desired acceleration, with the column mapping in host-audit.json. delivered-vertices.npz retains per-vertex samples for every method/resolution and overlap test. Metrics compare the same float32 grid at the same positions, using inclusive boundary-node trilinear weights and zero outside, as documented by official GAIA. CPU accumulation is not claimed bit-identical to GPU floating-point execution.

## Resolution and reconstruction comparison

Candidate: normalized trilinear splatting to eight corner nodes, followed by deterministic nearest occupied-node extension into empty nodes. No smoothing gain, ML system, altered water/occupancy or estimator is used. Both methods preserve constant vectors; all 24 constant checks have maximum error below 1e−6 m/s².

The table aggregates the worst statistic across the 12 states/fields, rather than hiding a difficult field in a global average. Pass counts apply to the supplied diagnostic vertices only.

| Method | Longest-axis nodes | Supplied-point passes | Worst mean relative | Worst P95 relative | Worst maximum relative | Maximum direction ° | Maximum absolute m/s² | Maximum dry-vertex force m/s² |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| idw4 | 32 | 6/12 | 4.1340% | 27.9424% | 53.6547% | 13.0517 | 2.283986 | 2.283986 |
| idw4 | 64 | 9/12 | 1.2497% | 8.2547% | 15.9310% | 3.7787 | 0.700049 | 0.625681 |
| idw4 | 100 | 9/12 | 0.4095% | 2.9328% | 9.3018% | 1.6330 | 0.239086 | 0.236055 |
| idw4 | 150 | 9/12 | 0.1840% | 1.0829% | 4.0134% | 0.6792 | 0.112748 | 0.108049 |
| normalized_splat | 32 | 12/12 | 0.1373% | 0.5020% | 0.5815% | 0.3330 | 0.041378 | 0.000000 |
| normalized_splat | 64 | 12/12 | 0.0000% | 0.0000% | 0.0000% | 0.0000 | 0.000001 | 0.000000 |
| normalized_splat | 100 | 12/12 | 0.0000% | 0.0000% | 0.0000% | 0.0000 | 0.000001 | 0.000000 |
| normalized_splat | 150 | 12/12 | 0.0000% | 0.0000% | 0.0000% | 0.0000 | 0.000001 | 0.000000 |

Normalised splatting fits these non-overlapping supplied targets much better. This does not certify between-vertex delivery or behavior while vertices move. Resolution improves IDW, but its mathematics still mixes the targets and bleeds force at dry vertices.

## Local domain and motion padding

Push domain is the current participating cloth bounds, independent of the entire FLIP domain. Per-axis padding includes .001 m numerical margin, maximum current estimated velocity, analytic water velocity, 1/48+.01 s control/native-overshoot window, desired acceleration and gravity contribution. Dimensions preserve near-isotropic spacing and use longest-axis targets 32/64/100/150. Bounds and actual dimensions are retained for every test.

This is a host prospective-motion budget. Unknown collision/elastic impulses are not bounded by this formula; no native claim that a future vertex cannot leave the box is made.

## Dry leakage: supplied vertices versus unsampled cloth

Splatting gives exactly zero at supplied dry vertices, but nearest occupied-node extension can apply large forces to dry points between samples. Additional probes follow actual piecewise-linear cloth edges outside the analytic strip. “Far” excludes the first two grid spacings from the wet boundary, so this is not merely one-cell legitimate boundary interpolation.

| Resolution | Force at supplied dry vertices | Maximum dry off-vertex force m/s² | Maximum far-dry force m/s² |
|---:|---:|---:|---:|
| 32 | 0.000000 | 16.700052 | 0.000000 |
| 64 | 0.000000 | 16.700052 | 9.464529 |
| 100 | 0.000000 | 16.700052 | 16.700052 |
| 150 | 0.000000 | 16.700052 | 16.700052 |

The high-resolution candidate still reaches approximately 16.7 m/s² on dry off-vertex points. Thus passing reconstruction at currently supplied vertices does not establish controlled dry-region delivery throughout a control interval. No occupancy workaround or new source scaling was introduced after observing this failure.

## Different objects / official group isolation

The documented ForceField.grid(groups=...) and official resolve_targets helper produce independent masks 1 and 2 for two coincident object point sets. At exactly the same position, stationary A receives 9.02705574 m/s² while already-matched B receives zero. Cross-group contamination is exactly zero in this official host targeting test. The native kernel checks the target bit before sampling each grid; no private mask or built-scene context was changed.

This verifies the supported host routing, not a new native two-object dynamics run. Current public Object.group selects a whole object; update_force_field uses the built scene group map. Separate objects can therefore receive distinct fields; the same single cloth object cannot dynamically label its overlapping layers through that interface.

## Mandatory single-object self-overlap / representability gate

The folded fixture is one connected mesh with 561 vertices and 1024 triangles, sharing a 17-vertex top hinge. The second panel folds back onto the first; velocity difference tapers to zero at the shared hinge. The zero-gap geometry is a limiting architectural test, not a valid collision-resolved native initial state. Additional host layered cross-sections test .2 mm, 1 mm and 5 mm gaps. Connectivity was independently checked.

| Layer gap m | Best tested resolution by maximum absolute error | Maximum absolute error m/s² | Maximum relative error | Passes |
|---:|---:|---:|---:|---:|
| 0 | 64 | 4.513528 | 750.00% | 0/4 |
| 0.0002 | 150 | 4.479570 | 744.36% | 0/4 |
| 0.001 | 150 | 4.338470 | 720.91% | 0/4 |
| 0.005 | 150 | 3.477510 | 577.85% | 0/4 |

At a coincident pair with water +X1 m/s and cloth velocities 0 and +X1 m/s, the desired accelerations are **9.027055 and 0 m/s²**. Any single-valued a(x,t) returns the same acceleration to both. Even its optimal minimax compromise has at least **4.513528 m/s²** error. This contradiction applies to every resolution and every reconstruction method.

To stay within the 20% maximum target error on the stationary layer requires at least 7.221644 m/s². Applying that same correction to the already-matched layer contributes at least a **1.150451 speed ratio after one nominal tick**, exceeding 1.01. This is an analytical Water-Push contribution, not a measured instantaneous native speed. Preserving the matched layer's 1% limit would permit only .48 m/s² and grossly miss the slow layer's target.

Distinct positive-gap points are not mathematically identical: in principle an arbitrarily steep field could distinguish them. At .2 mm the exact target jump requires about 45135 m/s² per metre. The tested bounded grids still mix those layers badly. Folded/contacting multilayer cloth is materially relevant to general cloth, not a rare out-of-domain point. Object group separation does not resolve the same-object case.

## Stop and deliberately unrun phases

The supplied request explicitly says: “If this limitation is significant in realistic cloth: STOP and report it. Do not hide it with grid resolution.” The mandatory architectural gate fails, and off-vertex dry leakage remains uncontrolled. Further tuning on this architecture is stopped.

- Native heterogeneous validation and the new ≤1.01 heterogeneous speed test: not run; no claim that the old estimated 1.039 issue is fixed.
- New 48-Hz constrained comparison (air-only/current/improved): not run after the stop. The preceding 24-Hz observed .015/.413 m transport comparison remains historical evidence, not a new success result.
- Real FLIP host reconstruction and full original-scene simulation: not run after the representability/dry-delivery failure. Original FLIP/cache/scene data remain untouched.
- New native encoding/export and additional held-update overhead: not measured after the stop. Only host construction and byte encoding are measured below.

## Host performance at the frozen 48-Hz working point

| Method | Resolution | Maximum final field MiB | Maximum construction ms | Maximum host byte encoding ms |
|---|---:|---:|---:|---:|
| idw4 | 32 | 0.059 | 4.604 | 0.033 |
| idw4 | 64 | 0.375 | 21.826 | 0.106 |
| idw4 | 100 | 1.373 | 66.518 | 0.208 |
| idw4 | 150 | 4.377 | 201.050 | 0.498 |
| normalized_splat | 32 | 0.059 | 1.035 | 0.002 |
| normalized_splat | 64 | 0.375 | 4.572 | 0.061 |
| normalized_splat | 100 | 1.373 | 10.172 | 0.250 |
| normalized_splat | 150 | 4.377 | 30.656 | 0.613 |

The per-control budget is 20.833 ms. A final field is at most about 4.38 MiB in this matrix; intermediate working allocations are additional and no full-process RSS claim is made. High-resolution construction can exceed the entire budget before native export/held overhead. Byte encoding is an in-memory contiguous serialization measurement, not a disk/native update timing. Lower resolutions are cheaper, but correctness and architectural failure prevent a practical product claim or default selection.

## Integrity and verification

All 14606 official packaged files are byte-identical to the original ZIP: zero differences. ZIP SHA-256 `fd3f43aa7e9849526b5113da558443a074039231ba700edfc953b6c83eb990d4`. No native solver was executed for this spatial task.

75 targeted existing controller/held-stream/Water Flow/PC2 regression tests passed. Additional numerical checks cover 24 constant-preservation cases, 96 supplied-target reconstruction cases, 16 overlap cases, official group-mask isolation and connected folded topology. Production code was not modified, so a full product regression was not run.

**Final answer: C for general deforming cloth under the current single-object group-targeted spatial API.** Restricted non-overlapping point targets and separate object groups are useful demonstrations, not completion of the stated general-cloth requirements. A future architecture would need supported per-vertex/velocity-aware coupling or genuinely separate material-layer identity; this audit does not implement or propose a solver patch.
