# GAIA 2.9.26: numerical stability investigation

17 October 2026. Investigation only. No package, runtime, physics, release,
version, channel or acceptance-threshold changes.

## Conclusion

The frozen owner package completes the functional test reliably, but the main
cloth is **not repeatable within the existing 8 mm material-vertex investigation
screen**. Three independent 2.9.26 executions produce pairwise main-cloth maxima
of 16.057–30.081 mm. The earlier 13.022 mm difference against 2.9.25 therefore
does not demonstrate a version-dependent regression. It lies inside the spread
observed without changing version. Three executions do not establish a
statistical distribution, a guaranteed repeatability bound, or a production pass.

The local difference includes an actual change in fold shape. It must not be
dismissed as tangential material-point movement on an unchanged surface. The
force-step pattern is reproducible and physically consistent with changing flow,
relative velocity and cloth orientation, but the exported diagnostics do not
prove that every component of every step is physical. No smoothing or threshold
relaxation is justified.

## Experimental controls

- A: preserved `published-2926-full-host` published-package test.
- B: new `stability-2926/repeat-b` execution.
- C: new `stability-2926/repeat-c` execution.
- R: preserved 2.9.25 owner `bake-long-r7`, retained as a separate reference.
- B and C run sequentially in separate Blender processes, from the same frozen
  NEON blend and the same installed published package; each has new output and
  fresh preparation. No direct-solver replacement for the regular Bake operator.
- Blender 5.2.2 LTS; NVIDIA RTX 4070 SUPER; driver 616.92. The desktop remained
  active; this is not a laboratory-controlled GPU scheduling experiment.
- Frames 130–159 plus actual fluid frame 160. Three original cloth objects,
  original materials, 186 attachments/four groups. CUDA, one-way coupling,
  rho=1000, Cn=1, Ct=0, original collisions and native SDF bounds protection.
- Published ZIP SHA-256:
  `382b14bb2b37f3a3bd7e576c051db535e6180d6f9e3d372c8d2a28fc447176c0`.
  All 275 installed package files match the archive. Runtime server/worker/DLL
  hashes and frozen blend hash match their approved baselines.
- All six pair comparisons have identical initial positions, corresponding
  topology, native binary inputs, prepared binary fluid fields, native
  `param.toml`, and hydrodynamic model. Random project/UUID paths are not used as
  geometric correspondence. UUIDs map back to each object's own material indices.
- A/B/C functional Bake + PC2 + save/reload: PASS. Native Bake durations:
  A 386.02 s, B 427.94 s, C 438.95 s (subsequent validation excluded).
  Runtime variation is not used as a stability acceptance metric.

## Repeatability measurements

RMS includes all corresponding material vertices and all 30 output samples,
including the identical initial sample. Maxima are over all vertices and frames.
Coordinates are native world metres, converted below to millimetres. Raw PC2
local coordinates are not treated as metres.

| Pair | Cloth | RMS mm | Maximum mm | Worst frame / local vertex |
|---|---|---:|---:|---|
| A–B | Main | 1.4354 | 30.0813 | 159 / 14600 |
| A–C | Main | 1.4649 | 16.0570 | 159 / 4726 |
| B–C | Main | 1.3410 | 28.2398 | 159 / 14611 |
| A–B | .001 | 0.0232 | 0.1674 | 136 / 2394 |
| A–C | .001 | 0.0171 | 0.1940 | 131 / 224 |
| B–C | .001 | 0.0216 | 0.3075 | 131 / 224 |
| A–B | .002 | 0.0404 | 2.0013 | 133 / 1278 |
| A–C | .002 | 0.0361 | 1.8598 | 134 / 1278 |
| B–C | .002 | 0.0400 | 1.7197 | 144 / 1772 |

The main-cloth maxima against R are A–R 13.0224 mm, B–R 29.7777 mm and C–R
14.9222 mm. The cross-version comparisons do not separate from the within-version
spread. No version-dependent force or input-field change was found in this test.

Main-cloth exceedances first appear at frame 139 (A–B), 135 (A–C), and 137 (B–C).
At frame 159, 266, 183 and 306 of 14,946 vertices respectively exceed 8 mm;
the largest fraction is 2.05%. Per-frame RMS, maxima, counts and explicit affected
vertex IDs are retained in `analysis.json`; flat tables are supplied as CSV.

![Per-object unsmoothed repeatability](C:/Users/timch/Documents/GitHub/Cloth_neXT/.owner-test-tmp/20261017/stability-2926/plots/material-vertex-repeatability.png)

## Localized geometry, contacts and attachments

The A–B maximum occurs in a fold on the lower main garment at Blender-world
(-0.18253, -0.04623, 0.53783) m in A. Its displacement has 26.724 mm normal and
13.811 mm tangential components relative to A's vertex normal. This decomposition
is not itself a closest-surface distance.

Bidirectional vertex-plus-triangle-centroid sampling at this worst frame gives
surface-distance maxima of **22.922 mm A→B** and **13.551 mm B→A**; RMS values
1.642/1.392 mm and p99 values 6.364/5.680 mm. These are unsigned sampled
point-to-triangle distances, not a certified Hausdorff distance, an all-frame
surface bound, or a pixel-level visual metric. Workbench close-ups visibly show
different local folding. A–C sampled maxima are 10.955/13.260 mm; B–C are
13.049/20.214 mm, at their respective worst material-vertex frames.

| Main-cloth maximum | Collider distance mm | Nonlocal self-surface distance mm | Actual attachment-anchor distance mm |
|---|---:|---:|---:|
| A–B in A | 34.305 | 12.884 | 80.468 |
| A–C in A | 23.639 | 9.645 | 347.462 |
| B–C in B | 5.286 | 4.107 | 71.589 |
| A–R in A, original 13.022-mm case | 12.748 | 10.765 | 337.727 |

Distances refer to the maximum point at the saved output frame. Self-surface
queries exclude faces touching the incident vertex ring; their result is
proximity, not proof of an active contact pair. Actual attachment distances use
cross-stitch source points and barycentric targets, not the kinematic collider
pins. The maximum is not directly at an attachment. The broader A–B affected
set includes points as close as 0.787 mm to the collider (configured triangle
contact gap 1 mm). Contact-sensitive folds are therefore relevant context, but
these data do not uniquely identify a particular self-contact event as the cause.

Incident triangles around the reported main maxima are fully wetted to float
roundoff, with zero local ±10-micrometre wet-classification sensitivity in the
last exported evaluation. This does not measure distance to the fluid surface
or exclude a historical fluid-boundary influence earlier in the trajectory.

Independent reciprocal cloth/collider edge-versus-triangle audits find zero
crossing segments in all 30 sampled frames for A, B and C. Endpoint exclusion
remains 1 micrometre. This does not certify self-contact, continuous-time
intersection freedom, or inside/outside classification of the open collider.

Workbench evidence contains six comparisons, common camera framing, front/side
details, heat maps, exact-vertex markers and inspectable `.blend` scenes. It is
generated from the actual native meshes in a new scene; original scene files are
not edited. Blue is zero material-vertex deviation; red is at least 8 mm.

![A and B, frame 159, identical camera](C:/Users/timch/Documents/GitHub/Cloth_neXT/.owner-test-tmp/20261017/stability-2926/plots/actual-fold-comparison.png)

## Numerical divergence and convergence

Differences start in the first linear solve, before the contact populations have
diverged. First reported residual A/B/C: 0.009986 / 0.009694 / 0.009921.
First maximum update: 0.01749 / 0.01743 / 0.01738 m. First accepted timestep:
approximately 0.003326 / 0.003319 / 0.003326 s. Initial contact count is 1222 in
all three; the next reported counts become 1620 / 1619 / 1613.

The measured explanation is early numerical trajectory branching followed by
different adaptive timesteps/contact configurations and growing fold differences.
Floating-point parallel accumulation/reduction order is a plausible mechanism,
not an isolated root-cause proof. No deterministic assembly experiment was run.
Native tolerance settings remain CG 0.01, constraint 0.01, CG cap 2500 and Newton
cap 2048. Recorded maximum individual linear iterations are A 455, B 456, C 378;
reported relative residuals remain at or below 0.01 to log precision. No solver
failure or evidence of hitting those caps appears in these runs. Meeting the
current algebraic tolerance does not guarantee geometric repeatability at 8 mm.

## Five force-step intervals

Values below are unfiltered sums of native normal-load telemetry. Indices are
native output indices, not Blender frame numbers.

| Interval | ΔF A N | ΔF B N | ΔF C N | Δwet A m² | Δmean fluid / relative speed A m/s |
|---|---:|---:|---:|---:|---|
| 1→2 | +13.301 | +13.676 | +14.117 | -0.029515 | +0.01929 / +0.06406 |
| 19→20 | +10.916 | +9.466 | +9.604 | +0.009431 | +0.02344 / +0.02393 |
| 20→21 | +17.702 | +17.285 | +17.883 | +0.013240 | +0.02412 / +0.02678 |
| 22→23 | -12.959 | -15.043 | -18.151 | +0.017135 | -0.04148 / -0.04030 |
| 23→24 | -19.430 | -20.256 | -15.898 | +0.013021 | -0.04744 / -0.04865 |

All intervals retain the same sign and overall flow-rise/flow-fall pattern across
the repeats. The main cloth dominates every step; .001 has essentially no fluid
load and .002 contributes much smaller changes. Startup includes rapid cloth
reconfiguration: wet-weighted normal rotation between outputs is about 14° then
11° in A, compared with roughly 4–6° around the later flagged region. Later
normal drag falls despite increasing wet area while sampled fluid and relative
speeds decrease. Quadratic normal drag also depends on local incidence and the
distribution of velocities; global mean speed cannot uniquely reconstruct it.

The exported collider positions and rigid transform tracks are effectively
stationary in this frozen segment. Animated-collider motion is therefore not an
explanation here, nor does this test validate response to a moving collider.
No convergence failure coincides with the five intervals. The 19→20 step falls
below the existing dynamic screening threshold in B/C; it remains investigated
in all runs rather than being dropped from the requested list.

Pairwise same-version maximum differences in force are 2.887 / 3.717 / 3.574 N;
wet-area differences are 0.001838 / 0.001657 / 0.001264 m² (A–B / A–C / B–C).
Loads span 91.04–145.98 N across A/B/C. All three pass unchanged finite,
nonnegative load/area, wet-area-bound, Ct=0 and Cn=1 drag-upper-bound checks.

Important timing limit: exported telemetry is the last native evaluation around
an output, not a force recomputed exactly at that output time. Pairwise evaluation
times differ by up to 7.749 ms within 2.9.26. For example A's output-20 evaluation
corresponds to source frame 150.2088, not exactly frame 150. Exact clocks and
resident fluid frames are preserved. There is no interpolation, smoothing or
time realignment used to remove flags in this report.

**Assessment:** the common force trend is physically consistent and is not
explained as an isolated erroneous spike in one run. It is not fully proved free
of numerical contribution. Full force vectors at the quadrature points, normal
incidence, and precisely aligned substep/contact-state histories are not all
available in the frozen diagnostics. Retain contextual-review status for those
questions; do not call the five intervals an unconditional physics pass.

![Unsmoothed force, wet area and speeds](C:/Users/timch/Documents/GitHub/Cloth_neXT/.owner-test-tmp/20261017/stability-2926/plots/native-force-and-wetting.png)

## Corrective work and broader acceptance

1. No evidence supports changing water occupancy, Cn/Ct, materials, collision
   settings, attachments, or smoothing forces to address these findings.
2. The existing 8 mm investigation screen is exceeded within one version, with
   visible local fold differences. A blanket repeatability/stability pass would
   be incorrect even though each Bake completes successfully.
3. A separate solver investigation should isolate deterministic accumulation/
   reduction ordering and linear-solve/adaptive-step sensitivity. A controlled
   tolerance experiment may establish an appropriate accuracy/performance tradeoff;
   none was performed here because frozen physics/settings were preserved.
4. Whether a solver correction is necessary for artist acceptance remains tied
   to the desired visual repeatability. For an 8 mm material-point repeatability
   requirement, the current implementation fails that screen and needs correction
   or further causal work; no threshold is changed here.
5. Before broader GAIA acceptance: characterize repeatability on more executions,
   moving colliders, other garments/materials and contact-heavy cases; examine
   continuous playback, fixed-time/substep forces and contact attribution; retain
   all prior CPU/material-zone, general reconstruction and runtime-distribution
   restrictions. Extending the full animation/last-frame lookahead is a separate
   task and was not silently substituted for 130–159.

## Evidence and reproduction

Final evidence root: `.owner-test-tmp/20261017/stability-2926/`.

- `repeat-b/`, `repeat-c/`: reports, PC2, saved blends and native session evidence.
- `analysis/analysis.json`: all six pairs, all frames/objects, affected IDs,
  world positions, sampled surface distances, proximity and native force data.
- `analysis/material-vertices-per-frame.csv`, `native-forces-per-object.csv`,
  `five-force-intervals.json`: reviewable numeric tables.
- `plots/`: unsmoothed standalone scientific figures.
- `viewport-evidence/views.json`, `pair-0.blend` through `pair-5.blend`: common
  framing, native geometry and evidence metadata. Pair order: A–B, A–C, A–R,
  B–C, B–R, C–R. Markers follow each run's actual material vertex.
- `repeat-b-collision/`, `repeat-c-collision/`, `native-consistency.json`:
  unchanged independent geometry and native telemetry checks.
- `installed-package-integrity.json`, `runtime-and-blend-integrity.json`,
  `gpu-environment.txt`: frozen artifact/environment checks.

Intermediate `prior-comparison` and `first-repeat-comparison` files are retained
as work evidence. Their early nearest-pin measurement refers to kinematic pins,
not actual cloth attachments. The final analysis explicitly separates those
and computes attachment proximity from cross-stitch sources/barycentric targets.

New diagnostic tools: `investigate_gaia_2926_stability.py`,
`export_gaia_2926_stability_tables.py`, `plot_gaia_2926_stability.py`, and
`blender_gaia_stability_views.py`. These read outputs or create independent
diagnostic scenes; no simulation implementation was changed. No publication,
deployment, commit, tag, or production certification occurred.
