# Isolated Water Push: velocity estimation and cadence audit

C — HIGHER CONTROL CADENCE SOLVES THE UNIFORM-FIXTURE FAILURE; general production coupling remains unvalidated.

No production integration, UI, defaults, external solver edits or release. These are controlled experiments, not product certification.

## 1. Public velocity capability — C: output positions only

Official GAIA 0.23 exposes output-frame vertices, object statistics and public numeric logs; no exact per-vertex velocity getter, previous native-step vertex getter or documented checkpoint-state inspection was found. Native checkpoint structures and private buffers were not read. The statistics implementation derives velocity from consecutive output-center positions × FPS. It is NOT an independent instantaneous oracle, even for a translating patch. The preceding audit’s labels were corrected; historical ~1.30 values are output-average peaks.

The independent free-patch reference is known acceleration impulse over actual public native clock/step times, with zero aerodynamic force and uniform translation. It is checked against semi-implicit integrated positions and exact-time output interpolation. A case whose maximum position residual exceeds the predeclared 0.0002 m threshold is reference-inconclusive, regardless of nominal speed pass. This is an analytically validated fixture reference, not publicly measured native vertex velocity or a general deformed-cloth oracle.

## 2–3. Estimators and measured errors

Secant: s1=(x2−x1)/h1. Nonuniform quadratic: s0=(x1−x0)/h0; v2=s1+h1/(h0+h1)×(s1−s0). Quadratic acceleration: a2=2(s1−s0)/(h0+h1). Optional native-time extrapolation adds a2×(held_time−output_time). For equal spacing this is (3x2−4x1+x0)/(2h). Startup uses known rest then secant until three outputs exist. That startup lag is significant at tau .05. Skipped/overshot frames use their actual sample times.

| Tau s | Estimator | Mean absolute error m/s | Maximum error m/s | Mean relative error (v>0.05) | Equivalent acceleration lag s | Mean error near target m/s |
|---:|---|---:|---:|---:|---:|---:|
| 1 | secant | 0.012330 | 0.024000 | 0.0441 | 0.021517 | not reached |
| 1 | bdf2 | 0.002030 | 0.024000 | 0.0043 | 0.000961 | not reached |
| 1 | quadratic-hold | 0.003780 | 0.024000 | 0.0115 | -0.004616 | not reached |
| 0.1 | secant | 0.017276 | 0.240000 | 0.0278 | 0.020799 | 0.004167651438313388 |
| 0.1 | bdf2 | 0.011469 | 0.240000 | 0.0207 | -0.005566 | 0.003137699628866193 |
| 0.1 | quadratic-hold | 0.014952 | 0.240000 | 0.0251 | -0.017274 | 0.004813317409476236 |
| 0.05 | secant | 0.032389 | 0.464516 | 0.0318 | 0.023759 | 0.02656940743756433 |
| 0.05 | bdf2 | 0.033408 | 0.464516 | 0.0345 | 0.002627 | 0.03258116494249938 |
| 0.05 | quadratic-hold | 0.042847 | 0.464516 | 0.0445 | -0.024230 | 0.03731120598860677 |

Errors above are 24 Hz / dt .01 with the original clipped controller. Equivalent lag is projected velocity error divided by the preceding positive applied acceleration (>0.2 m/s²); it is a trajectory-dependent diagnostic, not a constant filter delay. Near-target error uses 0.8<vX<1.02. Complete estimator/control traces and before-peak acceleration appear in summary.json.

## 4. Discrete controller

alpha=−expm1(−ΔT/tau); intended Δv=alpha×occupancy×(water−estimated_cloth); acceleration=intended Δv/ΔT. ΔT is the nominal requested control period. Actual held intervals and overshoot are independently recorded; they are not silently treated as equal. The exact-state/no-other-force law approaches the target without crossing only when applied for its specified interval. No instantaneous native state is supplied to this host controller.

## 5. Measured bandwidth relationship

| tau / nominal interval | Tau s | Reference maximum flow ratio | Gate |
|---:|---:|---:|---|
| 1 | 0.041667 | 1.126018 | overshoot |
| 2 | 0.083333 | 1.000000 | pass |
| 3 | 0.125000 | 0.999915 | pass |
| 4 | 0.166667 | 0.999227 | pass |
| 5 | 0.208333 | 0.997102 | pass |
| 8 | 0.333333 | 0.975633 | pass |
| 10 | 0.416667 | 0.949473 | pass |

For quadratic-hold/discrete at 24 Hz, dt .01, ratios 1 and 1.2 fail; tested ratios 2,3,4,5,8,10 pass. Thus K=2 is the smallest passing tested integer ratio for this fixture/timing, not a universal minimum or a production default. The actual largest held interval is .05 s here: tau .1 provides two observations over that worst interval; .05 provides only one. Finer native dt and different cadence can pass different nominal ratios. There is no dt-independent K established for deforming cloth.

## 6–7. Native dt and cadence

Official builder.rs clamps dt to min(requested_dt,0.9999/fps). A rejected 4800-Hz measurement trial demonstrably reduced .01 to ~.0002083125; it was excluded and did not provide an oracle. Valid cadence experiments retain requested native dt. At 120 Hz, dt .01 would become .0083325 and is excluded from unchanged-physics results. FPS and frame count together preserve duration; holds occur only after native steps and can overshoot. Public clock first coordinates provide actual native held time; frame/fps is exact output time. Native numeric log coordinates are rounded and are not presented as exact private state.

| Control Hz | Requested native dt s | Actual held interval range s | Maximum overshoot s | Duration s |
|---:|---:|---:|---:|---:|
| 24 | 0.01 | 0.040000–0.050000 | 0.010000 | 1.25 |
| 120 | 0.001 | 0.008000–0.009000 | 0.000667 | 0.75 |
| 120 | 0.002 | 0.008000–0.010000 | 0.001667 | 0.75 |
| 24 | 0.001 | 0.041000–0.042000 | 0.000667 | 0.75 |
| 24 | 0.002 | 0.040000–0.042000 | 0.001667 | 0.75 |
| 48 | 0.001 | 0.020000–0.021000 | 0.000833 | 0.75 |
| 48 | 0.002 | 0.020000–0.022000 | 0.001833 | 0.75 |
| 48 | 0.01 | 0.020000–0.030000 | 0.010000 | 0.75 |
| 60 | 0.001 | 0.016000–0.017000 | 0.000667 | 0.75 |
| 60 | 0.002 | 0.016000–0.018000 | 0.001333 | 0.75 |
| 60 | 0.01 | 0.010000–0.020000 | 0.010000 | 0.75 |
| 96 | 0.001 | 0.010000–0.011000 | 0.000917 | 0.75 |
| 96 | 0.002 | 0.010000–0.012000 | 0.001917 | 0.75 |
| 96 | 0.01 | 0.010000–0.020000 | 0.010000 | 0.75 |

## 8–10. Speed gate and response times

| Hz | dt s | CG tolerance | Tau s | Maximum impulse-reference flow ratio | Position residual m | Gate status |
|---:|---:|---:|---:|---:|---:|---|
| 24 | 0.01 | 0.001 | 0.1 | 0.999984 | 3.09494e-06 | pass |
| 24 | 0.01 | 0.001 | 0.05 | 1.029767 | 1.93552e-05 | overshoot |
| 120 | 0.001 | 1e-05 | 0.1 | 1.000807 | 0.000140244 | pass |
| 120 | 0.001 | 1e-05 | 0.05 | 1.004297 | 0.000986435 | reference-inconclusive |
| 120 | 0.002 | 1e-05 | 0.1 | 0.999507 | 1.12321e-05 | pass |
| 120 | 0.002 | 1e-05 | 0.05 | 1.000925 | 0.000167449 | pass |
| 24 | 0.001 | 1e-05 | 0.1 | 0.998949 | 0.00030477 | reference-inconclusive |
| 24 | 0.001 | 1e-05 | 0.05 | 1.004305 | 0.000973993 | reference-inconclusive |
| 24 | 0.002 | 1e-05 | 0.1 | 0.999200 | 2.45629e-05 | pass |
| 24 | 0.002 | 1e-05 | 0.05 | 1.000596 | 5.71924e-05 | pass |
| 48 | 0.001 | 1e-05 | 0.1 | 1.000462 | 0.000123981 | pass |
| 48 | 0.001 | 1e-05 | 0.05 | 1.002772 | 0.000512593 | reference-inconclusive |
| 48 | 0.002 | 1e-05 | 0.1 | 0.999378 | 1.40108e-05 | pass |
| 48 | 0.002 | 1e-05 | 0.05 | 1.000372 | 2.89127e-05 | pass |
| 48 | 0.01 | 1e-05 | 0.1 | 0.999502 | 2.53204e-06 | pass |
| 48 | 0.01 | 1e-05 | 0.05 | 1.000022 | 9.82838e-07 | pass |
| 60 | 0.001 | 1e-05 | 0.1 | 1.000887 | 0.000248615 | reference-inconclusive |
| 60 | 0.001 | 1e-05 | 0.05 | 1.001754 | 0.000249104 | reference-inconclusive |
| 60 | 0.002 | 1e-05 | 0.1 | 0.999580 | 6.14532e-05 | pass |
| 60 | 0.002 | 1e-05 | 0.05 | 1.000798 | 0.000131531 | pass |
| 60 | 0.01 | 1e-05 | 0.1 | 0.999520 | 3.62624e-06 | pass |
| 60 | 0.01 | 1e-05 | 0.05 | 1.000025 | 5.0751e-06 | pass |
| 96 | 0.001 | 1e-05 | 0.1 | 1.001444 | 0.000280378 | reference-inconclusive |
| 96 | 0.001 | 1e-05 | 0.05 | 1.002873 | 0.000592551 | reference-inconclusive |
| 96 | 0.002 | 1e-05 | 0.1 | 0.999221 | 6.75669e-05 | pass |
| 96 | 0.002 | 1e-05 | 0.05 | 1.000408 | 5.95903e-05 | pass |
| 96 | 0.01 | 1e-05 | 0.1 | 0.999503 | 2.17314e-06 | pass |
| 96 | 0.01 | 1e-05 | 0.05 | 1.000016 | 1.08362e-06 | pass |

The declared ratio remains ≤1.01. Old 24-Hz secant/clipped tau .05 output average is ~1.30144; the corrected independently integrated native reference peaks ~1.35234. Discrete secant reduces its peak to ~1.12039; BDF2 reduces it to ~1.02977, still failing at dt .01. Supported higher cadence is evaluated above without changing water speed or native dt.

At 48 Hz, tau .1 passes the reference check and ≤1.01 gate for all three requested native timesteps. This is the common validated useful range in the controlled fixture. dt .001 with CG .001 initially failed the position-reference check (up to ~5 mm); it was not silently counted as safe. The displayed finer-dt matrix explicitly tightens CG to 1e−5, which is a separate numerical-accuracy setting, not a Water Push default. Any remaining inconclusive cases are visible. It is therefore invalid to claim unconditional .1-s robustness across the original three solver presets. Longer runs, collisions and arbitrary deformation remain outside this controlled proof. Higher-rate trials cover .75 s; tau1 has not reached equilibrium within that time. Reference t63/t95 rise times are retained in summary.json rather than equating nominal tau with a fitted physical response.

## 11. Constrained and transition diagnostics

Reduced-topology production-like sheet: 2×2 m, 9×9 vertices, nine top-edge soft pins (strength 100), gravity, original material constants, dt .01, air density .001, tau .1. This is not the original 52×52 scene. Velocities below are output-history estimates; there is no independent instantaneous deformed-cloth oracle. Pin forces are not exposed; pin displacement is observable.

| Case | Mean +X displacement m | Center vX m/s | X deformation span m | Maximum estimated flow ratio | Maximum soft-pin displacement m | Maximum spatial acceleration delivery error m/s² |
|---|---:|---:|---:|---:|---:|---:|
| air_only | 0.014988 | 0.010480 | 0.023912 | 0.063852 | 0.076235 | 0.000000 |
| water_push | 0.413279 | 0.341773 | 0.594639 | 0.991557 | 0.059209 | 1.017967 |
| heterogeneous_push | 0.409014 | 0.363266 | 0.884125 | 1.038976 | 0.061695 | 1.678643 |

Uniform Water Push increases mean flow displacement from about .015 m to .413 m (~27.6×). The heterogeneous estimated flow ratio ~1.039 exceeds 1.01. Neither elastic-recoil attribution nor instantaneous speed safety is established for that case. The spatial delivery error reaches ~1.68 m/s². This is a diagnostic limitation, not a production pass. No total cloth-speed clamp is used.

Separate public groups test speed change, direction change, enter/leave water, two different spatial water speeds and unrelated dry acceleration at 96 Hz / dt .01 / tau .1. The known-field impulse reference remains limited to free uniform translation. All six independent position-reference checks pass; largest residual is about 12.2 micrometers. Direction-change old momentum is preserved and is not misclassified as newly generated overshoot.
- speed_change: final reference velocity [0.9965059640869126, 9.597780868730863e-08, 0.0]; last intended Δv [0.0003298200897213536, -4.969257485792046e-09, 0.0]; source interpolation is retained in each row.
- direction_change: final reference velocity [0.022327173886265286, -9.031198048958801e-07, 0.9770156254494539]; last intended Δv [-0.0021101032713744397, -9.176482504054235e-08, 0.0021655292732951767]; source interpolation is retained in each row.
- enter_leave: final reference velocity [0.9746288727995926, 1.2347126712509514e-08, 0.0]; last intended Δv [0.0, -0.0, 0.0]; source interpolation is retained in each row.
- region_slow: final reference velocity [0.4996959223505109, -1.848107179291958e-08, 0.0]; last intended Δv [2.905737632607214e-05, -2.1811330465277692e-09, 0.0]; source interpolation is retained in each row.
- region_fast: final reference velocity [1.9987836905941363, -3.1238369118113953e-08, 0.0]; last intended Δv [0.00011622950530428857, 2.624785270574299e-09, 0.0]; source interpolation is retained in each row.
- unrelated_dry: final reference velocity [0.0, 0.3700000000000002, 0.0]; last intended Δv [-0.0, -0.0, 0.0]; source interpolation is retained in each row.

## 12. FLIP timing / PC2 separation

The existing real .gaia frames 50 and 51 (24 fps) are sampled at the real cloth interior point and linearly interpolated at physical subframe times; source velocities are not rescaled and no additional FLIP bake samples are requested. Endpoint indices, alpha, raw velocity, support and existing encoded support-weighted contribution are separately retained in timing.json. This real-cache check is host-side; the native transition test uses synthetic 24-Hz source pairs to validate public temporal-grid interpolation independently. The original Blender/FLIP scene was not rebaked or changed.

48/96/120-Hz output grids contain every exact 24-fps sample: output index j=i×Hz/24. Selected native vertices use the existing centralized solver-to-Blender coordinate transform before the existing PC2 writer records Blender start frame1, sample_rate1. Round-trip positions and timing have zero measured error. 60 Hz contains only even scene-frame indices; full exact 24-fps selection is unsupported on that output grid. A common 120-Hz output grid could support 60-Hz control for dt .001/.002, but would alter dt .01 and is not claimed implemented. Timeline duration and source timing remain physical seconds.

## 13. External solver integrity

14606 official packaged files compared byte-for-byte against the original ZIP: 0 differences. Archive SHA-256 `fd3f43aa7e9849526b5113da558443a074039231ba700edfc953b6c83eb990d4`. Only additional runtime/audit data were created.

## 14. Regression / scope

75 targeted tests passed (7 prototype tests plus existing held stream, Water Flow, Blender descriptor and PC2 tests). No production module imports this experimental controller. Full product regression was not required or run because production code was not modified. Experimental tools and previous audit wording were changed. No UI/default/release/production changes were made.

The result supports continued isolated work on higher-cadence host control. It does not establish a safe production feature: exact deformed-cloth state, spatial delivery accuracy, constrained instantaneous speed validation, original-scene reproduction and long-duration/other-force safety remain unresolved.
