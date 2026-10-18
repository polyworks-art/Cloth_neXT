# Fast deterministic GAIA solver — results (2026-10-18)

Research checkpoint, not a release. Builds on `GAIA_NATIVE_REPRODUCIBILITY_RESEARCH_20261017.md`
(deterministic reference) and `GAIA_DETERMINISTIC_CONTACT_PROFILING_20261017.md` (profiling,
v1/v2 details). The ST-Tech upstream is untouched; nothing was published.

## Outcome

The deterministic reference was ~6x slower than the unordered solver on NEON. The fast
path is **bit-identical to the deterministic reference** wherever the reference itself is
deterministic, and costs between ~1.0x and ~3x of the unordered solver depending on scene
(NEON ~1.1–1.4x, timing range see below). It replaces single-thread serial execution with
exact parallel replays of the serial order; no formula, threshold, parameter or guard changed.

## Source and binaries

- Patch against the reference source (apply after `native-hydrodynamics-baseline.patch` and
  `native-research.patch`): `docs/validation/gaia026/reproducibility/native-fast-contact.patch`
  (5 files: `seam_cuda.cuh`, `research_order.hpp`, `backend.cu`, `kernelgen.py`, `operator.rs`).
- Local source copy `.hydro-test-tmp/reproducibility-20261017/fast-source`; reference source and
  reference binaries (`reference-deterministic/`, worker `f3a071af…`, DLL `9f4f156c…`) unchanged.
- Final binaries: CUDA backend `fast-v4/libsimbackend_cuda.dll` (`249ed4a5…`), worker
  `worker-v2/ppf-contact-solver.exe` (`55736ed6…`). Earlier states kept as `fast-v1`…`fast-v3`.
- Build recipes `build-fast-cuda.cmd`, `build-fast-driver.cmd` (research root).

## Switches (research opt-ins, still manual)

Reference semantics (serial ascending order): the five existing switches plus
`PPF_REPRO_SERIAL_DIRICHLET=1` (moving pins) and `PPF_REPRO_WALK_SPMV=1` (large systems).
Fast exact path: additionally `PPF_REPRO_FAST_CONTACT=1` and `PPF_REPRO_FAST_SERIAL=1`.

| Mechanism | What it fixes | How |
|---|---|---|
| fast contact (v1) | serial self-contact traversals (90 % of reference GPU time) | parallel count pass, prefix sums give each query the serial dynamic-slot base, parallel record pass logs float atomics, stable sort by address, ordered `atomicAdd` chain per address |
| fast serial replay (v2) | serial dynamic CSR fill/transpose, stitch/fixed-CSR push | generic: iterate on scratch copies until every element's integer atomic sequence is a fixed point of the serial return assignment, then the original parallel entry runs live with those returns; float atomics logged and applied in order; automatic fallback to the serial path if not converged in 4 iterations; live consistency check |
| Dirichlet lift (v3) | `dirichlet_lift_row`: moving pins lift onto free rows with float atomics (NEON's pins are static, so its lifts are zero) | added to the serial reference and to the replay |
| replay capacity guard (v4) | crash at very high contact density (racing scratch reads under provisional returns overran a buffer) | per-thread capacity from the count pass, bounded return lookups, mismatch marks the iteration unconverged |
| walk SpMV (worker v2) | PCG above 200,000 rows switches to a symmetric SpMV whose mirror half uses float atomics (`SYM_MIN_ROWS`) | keep the fixed-order walk at every size |

## Validation

NEON 130–159 (hydrodynamic, 77,860 vertices): every fast run with every version is byte-identical
in all 59 vertex/telemetry files to the five reference runs (v2 ×5, v3 ×1, v4 ×2, v4 A/B ×2, worker-v2 ×1).

Non-NEON benchmarks (upstream example scenes, no hydrodynamics, 60 frames; recipe
`recipes/build-contact-benchmarks.py`, runner `recipes/run-contact-benchmarks.py`):

| Scene | vertices | max contacts | unordered | reference | fast | fast = ref | fast = fast | ref = ref | unordered vs ref |
|---|---|---|---|---|---|---|---|---|---|
| sheets 32 | 7,682 | 1,237 | 35 s | 55 s | 43 s | yes | yes | yes | 10.9 cm |
| sheets 64 | 23,042 | 6,133 | 39 s | 120 s | 47–70 s | yes | yes | yes | 10.7 cm |
| sheets 128 | 84,482 | 31,381 | 59 s | 469 s | 70 s | yes | yes | yes | 10.8 cm |
| twist 120 | 22,748 | 64,420 | 14 s | 181 s | 16 s | yes | yes | yes | 5.7 cm |
| twist 240 | 90,616 | 251,629 | 22 s | 715 s | 72 s | yes | yes | yes | 7.7 cm |
| twist 360 (20 frames, walk) | 203,965 | 407,160 | 28 s | 262–292 s | 42–47 s | yes | yes | yes | 8.3 cm |
| twist 360 (60 frames, walk) | 203,965 | 653,883 | 70 s | (not rerun, ~35 min) | 233 s | n/a | yes | n/a | — |

Without the walk switch the twist-360 reference itself was not reproducible (two runs differ from
frame 1, up to 5.3 cm); stage captures showed bit-identical operator inputs and a differing first
`dx`, which located the symmetric SpMV.

"Bit-identical" means byte equality of every output vertex file (61 files per scene); deviations
are the maximum absolute vertex coordinate difference.

## Timestep sensitivity (separate from reproducibility)

All repeated runs at each dt are bit-identical, so the following is discretisation only
(`recipes/run-scene-timestep-convergence.py`):

| Scene | halving | max | final-frame RMS |
|---|---|---|---|
| twist 120 | 1/60 → 1/120 s | 14 cm | 3.6 cm |
| twist 120 | 1/120 → 1/240 s | 18 cm | 4.2 cm |
| sheets 64 | 0.01 → 0.005 s | 15 cm | 1.5 cm |
| sheets 64 | 0.005 → 0.0025 s | 21 cm | 1.7 cm |
| NEON (2026-10-17) | 0.01 → 0.005 → 0.0025 s | 31 / 25 mm | ~1.8 mm |

Successive halvings do not shrink the difference: fold/twist configurations are chaotic at this
scale. Reproducibility therefore requires identical dt and inputs; there is no converged
reference trajectory to compare against.

## Performance notes and caveats

- Timing on this desktop GPU varies with external load and clocks: identical NEON code measured
  54–102 s across the day; an interleaved v3/v4 A/B (71/75 s, 59/58 s) shows no difference. Use
  paired runs, not single numbers.
- Remaining determinism cost is mostly the generic replay's scratch copies and host syncs, and
  the Dirichlet replay per Newton step (twist 120: 16 s vs 14 s unordered).
- Coverage: an audit of all CUDA entries with float atomics found 47 without an ordering path
  (collision-mesh traversal, rods/Cosserat, rigid PDRD, translation locks, several scatter
  variants). NEON and the benchmarks use none of them except `dirichlet_lift_row` (now covered).
  Scenes with collision meshes, rods or rigid bodies are not covered yet and must be treated as
  non-deterministic until audited.
- Guarantees are per GPU/driver/toolchain; this is not cross-hardware certification.
- The fast paths are opt-in environment switches; automatic GAIA product wiring is open
  (planned within the AETHER/GAIA workflow, handled elsewhere).

## Evidence

`docs/validation/gaia026/reproducibility/fast-contact-20261018/` (controls, NEON runs, nsys kernel
tables, benchmark results v2–v5, timestep convergence). Raw outputs stay local under
`.hydro-test-tmp/`.
