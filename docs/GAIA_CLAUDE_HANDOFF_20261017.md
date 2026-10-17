# Claude handoff — GAIA native reproducibility (2026-10-17)

## Scope and safeguards

Continue the causal native solver correction and validation, then implement the already approved two-engine architecture: AETHER = latest verified official upstream; GAIA = separately packaged modified hydrodynamics solver. UX: select GAIA, install if needed, configure Hydrodynamics, bake. No owner manifests or NEON-only product restrictions. Do not publish, tag, change versions, update channels, alter the frozen 2.9.26 owner package, or touch the server repository without new authorization. Preserve one-way coupling, Cn=1/Ct=0, materials, attachments, collision behavior and SDF guard. Account editing remains exclusively in the Companion EXE; Blender displays status.

The owner explicitly authorized committing/pushing this research checkpoint to GitHub. This does not authorize releasing it.

## Source reconstruction

Add-on checkout: `C:/Users/timch/Documents/GitHub/Cloth_neXT`.
Native original checkout: sibling `ppf-contact-solver-hydrodynamics`, upstream HEAD `7232f4033515e1e72359f4ce5145456ce7d156f7`. Its original hydro modifications were not overwritten; its remote is ST Tech upstream and must not receive our push.

Research root B = `.hydro-test-tmp/reproducibility-20261017` under the add-on checkout. Modified native source = `B/native-source`.

To reconstruct on another machine, obtain the upstream commit above, then apply these patches IN ORDER from `docs/validation/gaia026/reproducibility`:
1. `native-hydrodynamics-baseline.patch` (previous native hydro work).
2. `native-research.patch` (five-file reproducibility correction and instrumentation).

`source-hashes.json` records modified file identities. Build recipes are in the same directory's `recipes/`; their toolchain paths are machine-specific. Never infer that a report alone contains all binary/runtime inputs.

## Causal findings and correction

- CPU `HashMap` traversal randomized derived edge/hinge IDs despite identical authored mesh input. Changed the two exported topology maps to `BTreeMap`, preserving face incidence and rods. Dedicated regression passed.
- First GPU arithmetic divergence was overlapping membrane assembly, not demonstrated PCG reduction instability. Conflict-free vertex-colored execution preserves original kernel bodies and per-vertex accumulation order.
- Further differences arose in self-contact force/fixed-Hessian scatter and dynamic CSR fill/transpose ordering. Original contact traversal now runs on refreshed scratch copies to identify contributing queries, then unchanged live traversal executes in stable query order. Original overlap reports/guards retained. CSR fill and transpose insertion ordered separately.
- Five full first-solve-only candidate runs STILL differed by up to 23.994 mm. Keep this failed evidence (`B/full-ordered-five`).
- Multi-Newton tracing proved the next first difference: assembly 2 stitch/fixed-CSR Hessian, three floats, maximum 1.5258789e-5. Ordered original stitch force and fixed-CSR push removed this later divergence.
- Cached scratch buffers and color schedules improve cost. Cache hits require full connectivity equality, arity and bounds; scratch views refresh every call. Explicit queue cleanup was added to backend close. Cached vs uncached corrected traces: all 354 files byte-identical.

No smoothing, artificial damping, physical parameter changes, hidden contact disabling or relaxed thresholds. There is no evidence requiring a PCG reduction rewrite when inputs are identical.

## Exact candidate and execution

Worker: `B/native-source/target/research-cuda/release/ppf-contact-solver.exe`.
Final clean CUDA DLL: `B/cached-cuda-build/lib/libsimbackend_cuda.dll`.
SHA256: `9f4f156c4116a8e1a0c516889648a60d8a68901daf964c5dd6d54f9f1953712b`.
Put its directory before the old runtime on PATH. Actual loaded-module path/hash is checked in the final suite, not merely the intended file.

Required research environment:
```
PPF_REPRO_ORDER_MODE=colored
PPF_REPRO_ORDER_FILTER=all
PPF_REPRO_FILTERED_CONTACT=1
PPF_REPRO_SERIAL_CSR=1
PPF_REPRO_SERIAL_STITCH=1
```
Full acceptance runs have all readback/early-stop probes OFF. These flags are currently research opt-ins; automatic GAIA engine wiring is unfinished.

NEON fixture: `B/neon-fixture/session`, unchanged original parameters, 30 output frames mapped to scene 130–159. Fluid model and volumes: `.owner-test-tmp/20261017/published-2926-full-host/session-evidence/hydrodynamic-aa366355bde442c3b8f0c2412edd3447`, true lookahead 160 included. Native-to-Blender transform is `[x,-z,y]`.

Final NEON suite: `B/full-corrected-cached-five` with manifest, resource samples, outputs and comparison. The final results appendix in the research report is authoritative; do not substitute exploratory results. Runner recipe `run-corrected-cached-five.py`.

## Completed evidence and limitations

CPU: 580 passed, zero failed, nine ignored performance benchmarks. Uncached corrected CUDA force/Jacobian/contact-scale regressions: two passed, frozen contact deltas zero. Independent non-NEON canvas/animated-plane scene: five uncached 60-frame runs, all ten pairs position/telemetry bit-identical, contact peak 409, minimum output-plane separation 0.936441 mm. This is not interframe CCD certification. Cached final reruns and SDF guard are recorded in the final appendix.

SDF original guard passed solo with the existing 60-second timeout. Earlier shared-GPU valid-case timeout is preserved, not erased. Do not overlap final performance runs with other GPU workloads. Recorded VRAM is whole-device memory, not process-exclusive allocation.

Large raw arrays, scene files, caches and runtime binaries remain LOCAL and are inventoried rather than published. Do not claim that proprietary NEON inputs are available from GitHub. Old frozen A/B/C evidence remains under `.owner-test-tmp/20261017/stability-2926` and published host directories. Published 2.9.26 itself reached about 30.081 mm pairwise material-vertex differences with true fold-shape changes.

## Next actions, in order

1. Inspect final comparison and all regression outputs, investigate any failures rather than weakening the existing 8 mm screening threshold. Bitwise results on this GPU/input do not certify other hardware/toolchains.
2. Run a genuine corrected-solver Blender Bake, PC2 export and playback in a fresh process, then independent collider intersection audit. These gates are NOT satisfied by native replays. Use an entirely new isolated runtime/profile and copy the frozen blend; never overwrite frozen binaries or source scene.
3. Existing harness: `tools/blender_neon_hydrodynamic_e2e.py`, actual regular `bpy.ops.clothnext.bake('EXEC_DEFAULT')`; existing collider audit `tools/audit_neon_collider_crossings.py`. Existing runtime/profile: `.hydro-test-tmp/blender-e2e-20261016/runtime`, `.owner-test-tmp/20261017/published-2926-profile`. Copy before modifying. Preserve three cloth objects, original materials and 186 attachments.
4. Frozen reconstruction dataset: `.owner-test-tmp/20261017/build-r6/local-runtime/neon-dataset.json` with sibling surfaces/bounds. Current research preparer is still owner-dataset-gated; a fixture-only adapter can pass this exact dataset for testing, but must not become the final product architecture.
5. Complete remaining controlled fixed/adaptive timestep sensitivity and fair runtime/VRAM comparison. Initial divergence preceded adaptive/contact population divergence; do not overstate experiments not performed.
6. Integrate standard AETHER/GAIA engine selection and installation after numerical gates. Relevant files: `cloth_next/updater/solver_registry.py`, `cloth_next/ppf/resolver.py`, `cloth_next/blender/solver_preferences_ui.py`, `cloth_next/gaia/hydrodynamic_session.py`. Registry has no engine field yet and UI currently calls all solvers GAIA. Existing managed solver validation targets official bundled manifests. Preserve compatibility and avoid inventing distribution/backend API behavior.
7. Only seek release approval after validated end-to-end behavior. No production certification claim.
