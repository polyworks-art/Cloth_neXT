# Native reproducibility research — 2026-10-17

Status: ongoing; no release or production acceptance.

## Causal findings

Identical NEON native input bytes did not imply identical derived CPU topology. `mesh.rs` exported edges and hinges by iterating randomized HashMaps. In two independent processes, 232,508 hinge property records differed by ordering while authored face records, initial positions and vertex properties matched. This changes element IDs, accumulation order, and downstream contact/CSR ordering.

The isolated research solver now builds derived edge/hinge maps using BTreeMap. Face orientation and face incidence are retained. A regression test checks canonical shared-edge ordering and retained incident face IDs; test execution is pending.

A separate GPU mechanism exists: overlapping floating-point atomic assembly in membrane kernels. Stable CPU topology alone still produces 23,720 different membrane matrix floats (max absolute 0.0078125 in matrix units) and 10,875 different force floats (max 8.73e-11 in force units) in the latest pair. These are arithmetic differences, not surface-distance metrics.

With stable CPU topology AND conflict-free colored GPU accumulation, the latest independent pair has bit-identical fixed matrices and force vectors at momentum, membrane, bending and hydrodynamics checkpoints. The same formulas and physical inputs are used. Coloring currently adds host readback/allocation overhead and is a research implementation, not an accepted performance result.

The contact/constraint aggregate still differs: 35 fixed-matrix floats, max absolute 0.00390625; 18 force floats, max 4.77e-7. The first linear solution differs by max 3.576e-5 m per scalar component. This is not a world-space trajectory or geometric surface-distance result. Further subdivision of strain, analytic/collider constraints and self-contact is being instrumented. No complete determinism claim is justified.

Evidence: `.hydro-test-tmp/reproducibility-20261017/topology-order-isolation-results.json`, `topology-order-{parallel,colored}/{0,1}/states`. Prior original-library evidence and all frozen 2.9.26 runs remain preserved. The intentionally stopped first-solve diagnostics are not successful bakes.

## Remaining gates

Locate and correct remaining accumulation/reduction differences; run meaningful topology regression; finish controlled timestep and solver-reduction experiments; run five complete independent corrected CUDA trajectories, force/wet-area/surface/collision comparisons, PC2 and fresh Blender playback; measure runtime and VRAM; validate an independent non-NEON scene. Only then revisit two-engine packaging. No packages, channels, physics settings, or frozen owner runtime changed.

## Further isolation

The stable-topology regression passed (1 passed, 590 filtered). The refined checkpoints show bit-identical strain-limiting and analytic/collider constraint matrix/forces. Divergence starts in self-contact assembly.

An explicitly diagnostic serial-query variant of the original self-contact traversals removes all fixed-matrix and force differences through Dirichlet elimination. First-solve displacement components still differ by up to 3.3035e-6 m. Dynamic contact CSR and transpose captures are being added to distinguish operator-input ordering from solver arithmetic. This is not yet evidence of a CG reduction defect.

The serial-query diagnostic costs ~11 seconds in contact assembly versus the short parallel diagnostic, so it is not accepted as the final performant implementation. It retains the original traversal, narrow phase, matrix insertion guards and physical parameters. No contact has been disabled or smoothed.

The dynamic capture isolates another pre-solve difference: identical dynamic CSR offsets/column indices (2,107 blocks), but 847 differing matrix components, plus 110 differently ordered transpose index/slot entries. The CSR fill uses float atomics for repeated contributions and the transpose uses atomic slot claims. Thus the remaining first-solve deviation cannot yet be attributed to CG reductions. An independent diagnostic switch now controls serial CSR fill/transpose, separately from serial self-contact queries.

## Causal first-solve control passed

With fixed CPU topology, colored overlapping elastic assembly, serial self-contact queries and serial dynamic CSR fill/transpose, two independent CUDA processes produce bit-identical force vectors, fixed and dynamic matrices, dynamic transpose and first-solve dx. This localizes the observed first-solve divergence to input/topology and accumulation ordering; this pair provides no evidence that a PCG reduction fix is necessary. It does not establish full-trajectory determinism.

The exact causal-control CUDA DLL is preserved in `causal-serial-csr-library/` beneath the research evidence root. A faster research experiment is being built: run the original traversal on scratch copies to identify contributing/overlap-reporting queries, then run the unchanged traversal on live buffers for those queries in ascending original order. This must match the all-query serial control before use in trajectory tests. Scratch results are discarded; diagnostic overlap reports and all original guards must remain active.

The faster contact-ordering experiment passed its first-solve control: all 88 captured files match the all-query serial reference byte-for-byte, and two independent filtered executions also match. Initial contact assembly was ~120 ms versus ~11 s for the all-query serial control. This is an early-stage cost measurement with readbacks, not a full-bake performance claim. The first optimization attempt rejected nonzero-offset arena spans and aborted; it is preserved as a failed diagnostic. The corrected version copies each validated typed buffer view on-device, respecting span offsets, without changing the real buffers.

Five complete independent NEON CUDA candidate runs have started with readback/intentional-stop probes disabled. Physical input hashes, worker/backend hashes, per-run runtime and whole-device GPU-memory samples are recorded in `full-ordered-five/manifest.json`. They are native replay runs, not yet Blender Bake/PC2 validation. The active DLL and frozen owner runtime are not being replaced during testing.

The first complete candidate replay succeeded (30 finite vertex outputs, 332.059 s; whole-GPU memory peak 6,464 MiB). This is not yet repeatability acceptance. Early outputs of run 1 differ from run 0 despite identical first-solve controls: max world material-vertex deviation 0.109 mm at frame 131 and 0.712 mm at 132. Later Newton assemblies therefore require further isolation. Stitch force and generic fixed-CSR block push remain unordered candidates, not yet proven causes.

A separate diagnostic library adds opt-in serial ordering for those two operations. It is built to `candidate-v2-library/`, leaving the active five-run candidate DLL unchanged. Short multi-Newton readback traces overlap the exploratory five-run suite; affected exploratory timing/memory measurements are not isolated performance evidence. A final corrected five-run acceptance suite still requires independent complete runs under controlled conditions.

## Corrected candidate and handoff checkpoint

Later Newton tracing proved an additional stitch/fixed-CSR atomic ordering defect: assembly 2 first differed in three Hessian floats (maximum 1.5258789e-5), before the next linear solve. The first-solve-only candidate therefore remained insufficient: its five full NEON runs reached 23.994 mm pairwise material-vertex deviation. This failed candidate and its results are retained, not accepted.

Serial ordering of the original stitch force and fixed-CSR insertion bodies removes the observed later divergence. Together with stable CPU topology, conflict-free colored elastic/hydrodynamic accumulation, ordered original contact traversals and ordered dynamic CSR fill/transpose, four Newton assemblies and the first output match exactly. No Cn/Ct, fluid occupancy, material, collision threshold, convergence tolerance, or damping change is involved.

A cache optimization retains per-queue scratch buffers and exact-connectivity-validated color schedules, with explicit queue synchronization and release at backend shutdown. All 354 captured state/output files matched the uncached corrected candidate byte-for-byte. The final clean CUDA build is `cached-cuda-build/lib/libsimbackend_cuda.dll`, SHA256 `9f4f156c4116a8e1a0c516889648a60d8a68901daf964c5dd6d54f9f1953712b`. Research ordering is opt-in through the five environment variables recorded in the execution recipe. Automatic GAIA product integration has not been implemented.

Completed regression evidence:

- Native CPU: 580 passed, zero failed, nine ignored performance benchmarks.
- Uncached corrected CUDA: two hydrodynamic force/Jacobian/contact-scale regressions passed; frozen contact deltas zero.
- Uncached corrected independent canvas/animated-plane fixture: five complete 60-frame runs, all ten pairs position and hydrodynamic telemetry bit-identical. Contact peak 409; minimum output-frame plane separation 0.936441 mm. This is not an interframe collision certification.
- Original SDF-bounds guard: solo valid/invalid test passed, original 60-second timeout unchanged. The preceding shared-GPU attempt timed out and remains preserved.

Final cached-candidate five-run NEON validation is recorded separately in `full-corrected-cached-five`; only its completed comparison may be used to assert full-trajectory repeatability. A loaded-module check verifies the actual CUDA DLL path/hash in every run. Whole-device VRAM includes other graphics clients and is not process-exclusive memory.

The source delivery consists of `native-hydrodynamics-baseline.patch` against upstream commit `7232f4033515e1e72359f4ce5145456ce7d156f7`, followed by `native-research.patch` against that hydrodynamic working tree. Apply in that order to reconstruct the modified native source. No native upstream repository was pushed. Binaries, proprietary NEON fixtures, and raw large caches remain local and are deliberately excluded from public Git.

Remaining gates include final cached-candidate regressions, a genuine corrected-solver Blender Bake with PC2 and fresh-process playback, collider audit, fixed/adaptive timestep sensitivity, and AETHER/GAIA product integration. No broader acceptance, packaging, release or production certification is claimed.

## Final cached-candidate NEON result

Five independent complete native CUDA runs of NEON 130–159 passed using the verified loaded DLL SHA above. All ten trajectory pairs are bit-identical: maximum world-space material-vertex deviation 0 m. With identical vertex positions/connectivity, geometric surfaces are identical as well; no approximate distance metric is needed to infer equality. All 870 compared per-object/frame hydrodynamic telemetry rows are bit-identical, including force and wet area. The existing 8 mm screening threshold is unchanged. This is repeatability evidence for this fixture/GPU/toolchain, not general production certification.

Runtime per complete native replay: 311.621–314.099 s, median 312.714 s. Maximum recorded whole-GPU memory: 6,811 MiB. These measurements do not establish process-exclusive VRAM or an apples-to-apples upstream performance comparison. Actual backend module identity passed for all five runs.

The final cached DLL also passed both CUDA hydrodynamic regression tests (force/nine Jacobian blocks and frozen contact scale, zero host/contact deltas). Corrected-solver Blender Bake, PC2/fresh playback, independent NEON collider audit, broader hardware testing and product engine integration remain open.

Final cached-candidate independent-scene and safety checks also completed: five 60-frame canvas/animated-plane runs, all ten pairs bit-identical in positions and fluid telemetry (maximum deviation 0 m); peak contact count 409 and minimum output-frame plane separation 0.936441 mm. Both final cached SDF cases passed: valid domain accepted and shifted/out-of-bounds SDF rejected by the existing native guard, with the unchanged 60-second timeout. No running native tests remain at this checkpoint. The independent fixture remains a native test, not a corrected Blender/PC2 acceptance result.
