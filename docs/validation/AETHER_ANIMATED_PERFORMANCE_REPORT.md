# AETHER Engine: high-performance animated character simulation (Milestone 5.3)

Date: 2026-11-01. RTX 4070 SUPER, Windows 11, Blender 5.2.3 LTS, the same inputs as Milestone 5.2.
Private test build only: no release, tag, version change or deployment. GAIA unchanged.

| component | baseline (5.2) | this milestone |
|---|---|---|
| AETHER Engine | `ff006e8` (build `88d640279111ed8a`) | `010740b` (build `1339fe67b78d7686`, API 1.2 unchanged, host protocol unchanged) |
| Cloth NeXt | `121fc90` (+ grab cursor `5a97866`, `22ad8e9`) | unchanged code; this report |

## Summary

**Optimize the work, not the physics.** Every optimization that is on by default produces **bit-identical
results** to the Milestone 5.2 engine: the PC2 caches of all three animated NEON presets, all three static
NEON presets and the 5.2 replay references are byte for byte equal. The speed-up comes entirely from doing
the same computation with less GPU work.

Animated NEON, 150 frames, baked through Cloth NeXt in Blender, both engines run back to back on the same
machine state (`m53_validate.ps1`):

| preset | 5.2 engine `ff006e8` mean / p50 / p95 / max (ms per frame) | 5.3 engine `010740b` | speed-up | end-to-end bake |
|---|---|---|---|---|
| Normal | 178.9 / 218.7 / 250.4 / 354.0 | **97.8** / 119.7 / 141.1 / 147.7 | **1.83x** | 32.4 → 20.3 s |
| Animation | 226.9 / 290.3 / 342.4 / 507.3 | **123.8** / 157.5 / 192.3 / 204.9 | **1.83x** | 39.5 → 24.1 s |
| Fitting | 221.8 / 256.8 / 354.5 / 541.2 | **141.6** / 161.0 / 229.3 / 285.7 | **1.57x** | 39.1 → 27.0 s |

Static NEON (248 frames): 12.9 / 18.9 / 25.3 ms per frame (Normal / Animation / Fitting), against
26.3 / 39.0 / 44.1 ms measured in Milestone 5.2 (that run was not back to back; the output is bit-identical).
GPU memory of the whole process at its peak (Normal): 5.14 → 4.39 GB (nvidia-smi, idle 3.53 GB).

Correctness: 0 cloth-body and 0 cloth-cloth intersections in **every output frame** of every preset, and
in **every substep** (diagnostics on: 1 824 / 2 484 / 2 980 substeps for Normal / Animation / Fitting, 0
new intersecting pairs). Resume after a kill is bit-identical for the animated (frame 75) and static
(frame 120) scenes. The experimental E4 recovery stays off by default and is not needed.

The exploratory target of 100-150 ms per frame for Normal is reached (97.8 ms) without any change to the
physics, the collision checks or the substep safety threshold.

**Recommendation: go** for the private artist beta, for walking-speed character animation like the test
clip. Not yet for running-speed limbs with tight or layered garments (section 8).

## 1. Baseline profile and bottleneck breakdown (5.2 engine)

Tools: `AETHER_PROFILE=<file>` (new, per-frame GPU time per phase in profile mode), Nsight Systems 2025.6.3
with `--cuda-graph-trace=node` (production graph mode), `replay.py` (dumped Cloth NeXt request through the
real BakeRunner, engine only) and the Blender bakes (end-to-end).

**Where the 7x comes from.** The collider substep factor: in Normal, 88 of 149 frames run at the maximum
factor 8 (mean 6.1). GPU time grows linearly with it (profile mode: 54 ms at factor 1, 117 at 2, 219 at 4,
450 at 8). The cost per substep, not the per-frame work, is what multiplies.

**Cost per substep** (Nsight, Normal, 40 frames, graph mode; 439 169 kernel launches):

| kernel group | share of GPU time | notes |
|---|---|---|
| CCD rounds (`k_ccd`, `k_ccd_pick`, `k_ccd_apply`) | **33 %** | 118 560 launches each; > 99 % of them empty (quiet rounds return at once, but each launch spans the whole contact capacity, ~4 600 blocks) |
| broad + narrow phase query (`k_grid_query`) | **33.5 %** | 1.07 ms per detection, ~4 detections per substep; edge queries (cloth edge vs collider edges and cloth edges) ~85 % of it |
| VBD sweeps | 10 % | |
| Newton-PCG / multigrid | ~12 % | |
| grid build, sorts, scans | ~7 % | |

Profile mode (graph replay off) per substep, Normal: CCD 12.0 ms, VBD 6.3, detection 5.8, PCG 1.8 ms.

**Scaling.** CCD launch cost scales with the contact *capacity* and the number of rounds (not the contacts);
the query with the number of cloth edges, collider edges near cloth and the swept box size (motion speed);
VBD and PCG with cloth vertex count; everything with the substep factor (motion speed).

**Host and Blender** (Normal, 5.2 engine, Blender bake 32.4 s for 149 frames): engine computation 26.7 s;
Blender side (scene validation, animated collider extraction for every frame, the per-frame quality
analysis of the test script) ~5.7 s; engine-host communication and PC2 writing ~1.7 s (replay: 33.5 s
end-to-end vs 31.8 s engine). Between frames the GPU waits ~11 ms per frame for the next collider frame.

**Collider motion.** Offline analysis of the animated NEON (`motion.json`): the fastest collider vertex moves
82 / 146 / 154 mm per frame (p50 / p90 / max). Restricting the measure to collider vertices near cloth does
not help (still 68-79 mm p50: legs and arms are under the garments, which explains the earlier failed
"near cloth only" variant). The motion *relative to the nearby cloth* is 4-5x smaller (19 / 26 mm), because
garments ride along with the limbs.

## 2. Evaluated strategies

| strategy | result |
|---|---|
| CCD rounds in one cooperative launch | **kept**: bit-identical, CCD 33 % → 2 % of GPU time |
| broad-phase table per item class | **kept**: bit-identical, query only walks classes it pairs with |
| cached item boxes, late vertex fetch in the query | **kept**: bit-identical |
| larger hash tables (8x instead of 2x items) | no measurable change, rejected |
| relative collider motion for the substep factor (+ post-frame check and frame repeat) | built and validated; NEON 4-21 % faster at 0 intersections, but worse in 2 stress scenes: **kept as experiment, off by default** |
| absolute motion only of collider parts near cloth | not repeated: the offline analysis shows no gain (see above) |
| coarser collider substeps (fraction 1.0 / 2.0) | not repeated (5.2: 2.0 explodes, 1.0 no margin) |
| higher maximum factor (16) for the failing stress scenes | does not fix them (section 8) |
| cooperative VBD sweeps | not done: the VBD kernels are occupancy-bound per colour (2.3 k vertices), not launch-bound |
| restricting carry re-check detections to carried vertices | not done: no safety argument for the pairs that are left out (section 12) |

## 3. Implemented GPU and solver changes (AETHER `01166e0`, `010740b`)

**Cooperative CCD cycle.** The truncate, slide and revert rounds of one CCD cycle (up to 130) used to be
three kernel launches per round over the whole contact capacity; after the first quiet round all later
launches returned at once, but still cost ~8 µs each. `k_ccd_cycle` runs all rounds of a cycle in one
cooperative launch (grid = resident blocks) with grid barriers between the three steps and stops at the
first quiet round. Loads of data other blocks change within the launch bypass L1 (`ld.cg`). The three step
bodies are shared `__noinline__` device functions with the old kernels, so both paths execute the same
compiled arithmetic: `AETHER_EXP_CCD_LEGACY=1` keeps the old launches, and the new engine test
`ccd_cooperative` checks bit-identity on every run. (With inlined bodies the compiler fused differently and
results differed by 1 ulp: found by a per-cycle hash comparison, fixed before acceptance.) Kernel launches
per 40 NEON frames: 439 169 → 84 401.

**Per-class broad-phase tables.** The hash grid had one bucket table for cloth triangles, cloth edges,
collider triangles and collider edges; a cloth-edge query walked every entry of its cells and skipped most
by type. Each class now has its own table; a query walks only the classes it pairs with. Same candidate
set, same contacts.

**Cached item boxes.** `k_grid_count` stores each item's swept box; `k_grid_fill` and `k_grid_query` read it
instead of recomputing it from the vertices and motion bounds; the query reads item vertices only after the
box and cell tests pass, and rejects duplicate self pairs before the box test.

**Relative substep measure (experimental, off).** `collider_substep_relative`: the factor is computed from
`min(|dc|, |dc - dx_i|)` over collider-cloth pairs whose relative motion can close their distance within a
per-vertex slack (cloth vertices sorted by cell on the GPU, `k_rel_query`), where `dx_i` is the cloth's
predicted motion. After the frame the measure is repeated with the actual cloth motion, and a frame that
needed more substeps is repeated from its saved state. It never runs more substeps than the absolute rule.

**Diagnostics and settings.** `AETHER_PROFILE=<file>` (per-frame phase times, detection counts, CCD
statistics, memory), `AETHER_SUBSTEP_LOG=<file>` (per-frame factor decisions), `AETHER_EXP_SUBSTEP_RELATIVE`,
`AETHER_EXP_MAX_SUBSTEP_FACTOR` (experiments). The M5.1/M5.2 collider settings
(`collider_edge_signed`, `collider_edge_contacts_all`, `collider_substep_edge_fraction`,
`max_collider_substep_factor`) and the new switch are now part of the parameter fingerprint.

Unchanged: physics, material interpretation, solver iterations, collision thickness, substep threshold
(0.5 x median edge per substep, max factor 8), contact sets, CCD rounds and their order, failure reporting,
host protocol, Cloth NeXt integration. No CPU fallback.

## 4. Rejected experiments

* **Relative substep measure as default.** NEON: Normal 100 → 96 ms, Animation 126 → 106, Fitting 144 → 114
  (replay, back to back), 0 intersections in every frame and every substep (Normal, Fitting checked), no
  frame repeats needed. But in the new stress scenes `elbow_layers` (1 374 / 15 520 collider / cloth
  intersections vs 376 / 1 852 with the absolute rule) and `leg_swing` (4 / 38 vs 0 / 19) it is worse. The
  brief requires a demonstrated safety margin for any change to the threshold; this is not one. Kept
  behind `AETHER_EXP_SUBSTEP_RELATIVE=1` for future work.
* **Larger broad-phase tables.** No change within measurement noise.
* **Max factor 16** for the failing stress scenes: elbow_run 212 → 92 collider edges but still failing,
  elbow_layers no better, 10-20 % slower. Not a fix.

## 5. Before / after timings

Blender bakes: see the summary table. Replay (engine only, back to back, idle GPU, Normal): 5.2 engine
183.4 ms → after the pipeline changes 99.6 ms per frame (p95 269.7 → 143.9, max 394.4 → 152.0).

Nsight, same 40 frames, after (graph mode; absolute times depend on the machine state, shares do not):
query 28-38 %, VBD 18-25 %, multigrid/PCG ~20 %, grid build + sorts ~10 %, **CCD 2-3 %**.

**Engine-only vs full workflow.** Animated NEON Normal: the engine computes 14.6 s of the 20.3 s Blender
bake; the rest is Blender (scene validation, reading the animated colliders every frame, and in these test
runs the per-frame quality analysis) and the engine-host exchange (~1.6 s). The Blender share is unchanged
by this milestone, so the end-to-end speed-up (1.6x) is smaller than the engine speed-up (1.83x).

## 6. CCD and collision statistics

The default path is bit-identical to 5.2, so contact counts, CCD impacts, reverts, rounds and unconverged
cycles are identical too (checked for the bench scenes `ball`, `elbow_tight` against the legacy launches:
10 421 / 3 443 reverts, 191 378 / 97 510 impacts, 180 / 6 unconverged cycles in both). Animated NEON
(Normal, profile): mean factor 6.1, 0 unconverged CCD cycles, at most 9 CCD rounds per cycle, carry
re-checks in 75 % of the substeps, ~80 k contacts per frame, solver memory 400 MB.

## 7. Animated NEON acceptance (5.3 engine, Blender)

| preset | ms per frame mean / p95 | collider / cloth-cloth, every frame | strain p99 max (3 garments) | substeps checked, new pairs |
|---|---|---|---|---|
| Normal | 97.8 / 141.1 | 0 / 0 | 17 / 24 / 21 % | 1 824, 0 |
| Animation | 123.8 / 192.3 | 0 / 0 | 14 / 25 / 22 % | 2 484, 0 |
| Fitting | 141.6 / 229.3 | 0 / 0 | 16 / 20 / 22 % | 2 980, 0 |
| Normal + experimental recovery | 107.2 / 149.4 | 0 / 0 | 21 / 36 / 22 % | - |

The strain values are identical to Milestone 5.2 (the caches are). Attachments stay on the rotating limbs,
Material Zones behave as before, all garments follow the animation, PC2 playback exact, resume
bit-identical, no orphaned engine processes. The original `F:\16_Cloth_neXt\SW04\SW04_NEON.blend` was not
touched.

## 8. Stress tests (`aether_collider_bench`, Normal, 71 frames, every frame checked)

New scenes: `elbow_run` (tight sleeve, elbow bends 100° and back every 10 frames, running arm),
`elbow_layers` (two sleeve layers 1 mm apart on the running elbow), `elbow_coarse` (5 cm cloth edges on a
fine elbow), `leg_swing` / `leg_swing_loose` (leg swinging ±40° at running cadence in tight / loose
trousers), `pulse_tight` (tight sleeve on a tube pulsing ±15 % every 6 frames), `fast_hand` (hand-sized
ball at ~3 m/s grazing and crossing the sheet), `four_balls` (four balls crossing the sheet at once),
`chase` (sheet falling onto a ball moving away, then the ball reverses into it), `ball_lowpoly`.

| scenario | ms mean / p95 / max | factor | collider / cloth-cloth max | at end | unconverged CCD cycles | MB | relative (exp.): factor, collider / cloth |
|---|---|---|---|---|---|---|---|
| ball | 7.0 / 10.4 / 14.1 | 1.00 | 0 / 0 | 0 | 180 | 19 | 1.00, 0 / 0 |
| fast_ball | 23.6 / 28.9 / 43.4 | 3.00 | 0 / 0 | 0 | 1 292 | 19 | 3.00, 0 / 0 |
| reverse_ball | 10.8 / 27.5 / 30.8 | 1.25 | 0 / 0 | 0 | 360 | 19 | 1.25, 0 / 0 |
| two_balls | 7.2 / 13.0 / 14.5 | 1.00 | 0 / 0 | 0 | 160 | 21 | 1.00, 0 / 0 |
| folded | 11.6 / 19.9 / 21.3 | 1.00 | 0 / 0 | 0 | 0 | 20 | 1.00, 0 / 0 |
| elbow | 8.1 / 16.7 / 19.0 | 1.39 | 0 / 0 | 0 | 1 | 16 | 1.01, 0 / 0 |
| elbow_tight | 8.5 / 13.3 / 21.3 | 1.39 | 0 / 0 | 0 | 6 | 18 | 1.01, 0 / 0 |
| twist | 4.8 / 9.0 / 9.4 | 1.00 | 0 / 0 | 0 | 0 | 33 | 1.00, 0 / 0 |
| twist_tight | 9.5 / 17.4 / 32.5 | 1.00 | 0 / 0 | 0 | 0 | 35 | 1.00, 0 / 0 |
| ridge_start (starts crossed) | 8.4 / 15.1 / 17.2 | 1.00 | 1 020 / 0 | 528 | 0 | 4 | 1.00, 1 020 / 0 |
| **elbow_run** | 224 / 498 / 522 | 8.00 | **212 / 843** | 118 | 3 626 | 18 | 6.00, 80 / 182 |
| **elbow_layers** | 360 / 481 / 497 | 8.00 | **376 / 1 852** | 174 | 4 253 | 32 | 7.82, 1 374 / 15 520 |
| **elbow_coarse** | 126 / 169 / 173 | 3.00 | **74 / 13** | 62 | 1 553 | 5 | 3.00, 74 / 13 |
| **leg_swing** | 93.5 / 195 / 286 | 6.73 | 0 / **19** | 0 | 1 007 | 40 | 5.96, 4 / 38 |
| leg_swing_loose | 48.9 / 80.7 / 89.8 | 6.73 | 0 / 0 | 0 | 264 | 36 | 6.56, 0 / 0 |
| pulse_tight | 8.4 / 14.6 / 19.1 | 1.00 | 0 / 0 | 0 | 0 | 19 | 1.00, 0 / 0 |
| fast_hand | 122 / 184 / 200 | 4.00 | 0 / 0 | 0 | 1 524 | 19 | 3.82, 0 / 0 |
| **four_balls** | 17.4 / 57.1 / 64.0 | 1.00 | **30 / 136** | 30 | 228 | 25 | 1.00, 30 / 136 |
| chase | 7.2 / 23.0 / 25.8 | 1.21 | 0 / 0 | 0 | 84 | 19 | 1.18, 0 / 0 |
| ball_lowpoly | 7.3 / 14.8 / 16.3 | 1.00 | 0 / 0 | 0 | 156 | 18 | 1.00, 0 / 0 |

All 10 Milestone 5.2 scenes are unchanged (0 intersections; `ridge_start` 1 140 → 528 at the end, as in 5.2,
not worse). **The failing stress scenes fail identically with the 5.2 engine** (checked with the legacy CCD
launches: same counts), so they are pre-existing limits, not regressions: running-speed elbow bends with a
tight sleeve or two layers (thousands of unconverged CCD cycles: the CCD round budget is exhausted and
reported), coarse cloth on a fine collider (the `ridge_start` discretization class), cloth folds in tight
trousers on a running leg, and cloth squeezed between colliders moving towards each other (`four_balls`,
the class-4 "trapped between surfaces" case). Doubling the maximum substep factor does not fix them.
Substep-level diagnostics were run for the animated NEON (all presets); for the bench scenes only output
frames are checked.

## 9. GPU memory

Solver allocations (`device_bytes`): animated NEON 400 MB (5.2: same buffers; the per-class tables replace
the shared table at about the same size, the item boxes add 9 MB, the relative-measure buffers 1 MB).
Whole process at its peak in the Blender bake (nvidia-smi): Normal 5.14 → 4.39 GB, Animation 5.62 →
4.50 GB, Fitting 5.40 → 4.60 GB (idle 3.53 GB). Bench scenes 4-40 MB.

## 10. Determinism and recovery

* Matching runs: the 5.3 engine reproduces the 5.2 caches bit for bit (animated NEON 3 presets x 3 garments,
  static NEON 3 x 3, replay references).
* Resume: animated NEON Fitting killed at frame 75 and static NEON Fitting killed at 120, both resumed
  bit-identical; e2e `kill_resume`, `cancel`, `device_lost` PASS.
* Compatibility: the build id changed (`88d640279111ed8a` → `1339fe67b78d7686`) and the parameter fingerprint
  now includes the collider settings, so 5.2 checkpoints are rejected explicitly (e2e `incompatible` PASS),
  never resumed. Engine test `checkpoint`: 3 invalid checkpoints rejected, 18 resumed frames bit-identical.
* No duplicate or missing PC2 frames, no corrupted metadata, no orphaned engine processes (GUI smoke, e2e
  `lifecycle`).

## 11. Regression gate

| check | result |
|---|---|
| AETHER engine tests | **35 / 35** (new: `ccd_cooperative`) |
| host integration tests | 10 / 10 |
| collider stress tests | 20 scenes, see section 8 (no regression; 5 pre-existing failures documented) |
| Cloth NeXt suite | 2 751 passed, 35 skipped (GAIA solver executable / local solver package not configured for the suite, POSIX-only tests, TETHYS engine builds not present, a release-migration artifact and a cloud fixture) |
| Blender e2e (static and animated collider, multi material, animated pins, sewing, Material Zones, attachments, Live, cancel, kill + resume, device lost, incompatible checkpoint, lifecycle, offset range, GAIA routing, performance) | all PASS |
| GUI smoke (visible UI) | 18 / 18 |
| Grab Cloth (event simulation, incl. cursor colour) | PASS |
| static NEON acceptance | 0 / 0, bit-identical to 5.2, resume bit-identical |
| animated NEON acceptance | 0 / 0 every frame and substep, resume bit-identical |
| real GAIA bake (unchanged GAIA backend) | PASS |

The skipped suite tests are external dependencies that are not part of this environment; the real GAIA
path is covered by the real GAIA bake instead. No test was excluded.

## 12. Remaining bottlenecks

Per substep after this milestone (Nsight): broad + narrow phase query ~30 % (cloth edge queries against
collider edges and other cloth edges), VBD sweeps ~25 % (occupancy-bound per colour), multigrid/PCG ~20 %,
grid build and sorts ~10 %. Per frame: the substep factor still averages 6.1 (Normal) on the animated
clip, so the animated scene is ~7.5x the static one per frame, as before.

## 13. Recommended next optimizations

1. A safe reduction of the substep factor: the relative measure is the right quantity for riding cloth;
   it needs a stronger safety argument for layered and tight cloth on fast joints (e.g. relative measure
   only where the post-frame check also confirms CCD convergence, or per-region factors with a proof of
   isolation).
2. Carry re-check detections (~25 % of all detections): restrict them to pairs whose motion left their
   detection box, with a proof that the other pairs stay checked.
3. Edge-edge query: warp-cooperative cell traversal or a sorted per-cell layout to cut divergence.
4. VBD: merge small colours, or a lane layout with more vertices per block.
5. Host: overlap the next collider frame's upload with the current frame (~11 ms idle GPU per frame).
6. Capability (not speed): the running-elbow and layered-sleeve stress scenes need a better answer than
   more substeps (CCD round budget, contact response for pinched layers).

## 14. Go / no-go for the private artist beta

**Go**, for: draping, fitting, static and moving colliders, and animated characters at walking speed like
the NEON test clip (tight multi-layer garments, attachments, Material Zones), in Normal, Animation and
Fitting. Bakes are ~1.6-1.8x faster than in 5.2 with identical results.

**Not yet** (document for testers): running-speed limb motion with tight or layered sleeves, coarse cloth
on detailed bodies, cloth squeezed between body parts moving into each other. These fail the same way as
in 5.2 and are reported (unconverged CCD cycles), not hidden. Experimental recovery stays off.

## Reproducing

```
# engine (AETHER repo, x64 VS environment)
cmake --build build/release
build/release/aether_tests.exe
python -m pytest -q tests/host/test_host.py
build/release/aether_collider_bench.exe ball fast_ball reverse_ball two_balls folded elbow elbow_tight twist twist_tight ridge_start elbow_run elbow_layers elbow_coarse leg_swing leg_swing_loose pulse_tight fast_hand four_balls chase ball_lowpoly
AETHER_EXP_SUBSTEP_RELATIVE=1 build/release/aether_collider_bench.exe ...      # experimental measure
AETHER_EXP_CCD_LEGACY=1 build/release/aether_collider_bench.exe ...            # separate CCD launches

# private test environment (C:\Users\timch\ClothNeXt-AETHER-Test)
m53_replay.sh <tag> normal|animation|fitting [--diag] [--env KEY=VALUE]       # replay + per-frame intersections
nsys_run.sh <tag> normal 40                                                   # Nsight kernel breakdown
m53_stress.sh <out.jsonl> [ENV=VALUE]                                         # all bench scenes
powershell m53_validate.ps1 -Tag m53v                                         # Blender acceptance (5.3 engine)
powershell m53_validate.ps1 -Tag m53_base -Engine "engine\AETHER Engine.ff006e8.exe" -Short   # 5.2 baseline
python tools/run_aether_blender_e2e.py --engine "<test env>\engine\AETHER Engine.exe" --output <dir>
```
