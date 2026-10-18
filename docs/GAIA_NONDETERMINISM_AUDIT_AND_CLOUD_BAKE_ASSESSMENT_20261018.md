# Remaining nondeterministic paths and Cloud Bake assessment (2026-10-18)

Research audit, no solver change in this step. Baseline: the fast deterministic path documented in
`GAIA_FAST_DETERMINISTIC_SOLVER_20261018.md` (worker `55736ed6…`, CUDA backend `249ed4a5…`, all
switches including `PPF_REPRO_WALK_SPMV`). No Cloud Bake implementation was started.

## 1. Classification of the uncovered float-atomic CUDA entry points

An enumeration of every generated CUDA entry with float atomics and no ordering path gave 46
entries after the walk-SpMV fix (the 47th, `operator_apply_symmetric_folded`, is avoided by
`PPF_REPRO_WALK_SPMV`). Call sites were resolved by searching the solver for each entry's argument
record (`<Entry>Args { … }`).

| Group | Entries | Reachable from the solver? | Feature that triggers it |
|---|---|---|---|
| Legacy/alternate scatter and pair-form kernels (`face_/hinge_/rod_/vertex_*_embed_*` except the two below, `contact_point_face` etc. pair forms, `contact_embed_*`) | 22 | no caller | — |
| `fixed_push_element_blocks*` | 5 | tests only | — |
| `hinge_live_embed_force`, `rod_live_embed_force` | 2 | yes | soft bodies (tets), rods; documented upstream as serial ascending passes; rods measured deterministic (below) |
| Collision mesh: `collision_point_face_m2c/c2m_traverse`, `collision_edge_edge_traverse` | 3 | yes (`driver/contact.rs`) | fully pinned static colliders (frontend `pin()` + `update_static()` → `static_vert/static_tri`) |
| Rigid bodies (PDRD): `pdrd_restrict_row`, `pdrd_rigidify_centroid_row`, `pdrd_fit_centroid_row`, `pdrd_fit_covariance_row`, `pdrd_assemble_sandwich_row` | 5 | yes (`driver/pdrd.rs`) | `as_pdrd()` bodies (add-on role RIGID_BODY) |
| Translation locks: `translation_lock_drift_row`, `lock_center_of_mass/inertia/row_sums/torque_accumulate_row`, `lock_constraint_assemble_row` | 6 | yes (`driver/lock.rs`) | `lock_translation` / `lock_all_translations` |
| Cosserat: `cosserat_shear/bend_row`, `cosserat_apply_slot/site_row`, `cosserat_schwarz_apply_row` | 5 | yes (`driver/cosserat.rs`, `driver/pcg.rs`) | framed (Cosserat) rods; not triggered by plain rods |
| `schwarz_restrict_row` | 1 | yes (`driver/schwarz.rs`) | `precond = "schwarz"` only (default is block-jacobi) |

So 20 reachable entries in four feature groups remain, plus the two documented-serial passes.

## 2. Measurements (two reference runs, two fast runs, 60 frames)

Scenes: `recipes/build-audit-scenes.py` (upstream frontend, no hydrodynamics). Runner:
`recipes/run-contact-benchmarks.py` with the walk switch.

| Scene | reference = reference | fast = fast | fast = reference | max. deviation ref/ref |
|---|---|---|---|---|
| static collider (3 sheets on a pinned static sphere) | no | no | no | 27 cm |
| PDRD (9 rigid balls on a pinned sheet) | no | no | no | 17 cm |
| translation lock (9 locked tet balls on a sheet) | no | no | no | 9 cm |
| rods (25 strands into a bowl) | **yes** | **yes** | **yes** | 0 |

First differing stage between two runs (stage captures, `PPF_REPRO_DIR`, first 40 Newton
assemblies):

- static collider: assembly 38, `constraints` stage, fixed matrix: the collision-mesh traversals
  deposit through the constraint path once the cloth reaches the static collider.
- PDRD: assembly 1, first linear solve `dx`: the PDRD restriction/sandwich reductions act inside
  the solve.
- translation lock: assembly 1, first linear solve `dx`: lock accumulations act inside the solve.

These groups are non-deterministic in the deterministic reference itself; the fast path inherits
it. They must be treated as non-deterministic until fixed.

Product relevance: Cloth NeXt Colliders with Collider Motion = STATIC are the most common
configuration and are likely exported as static collision meshes; NEON's collider is animated and
therefore runs through the (covered) deformable-mesh path. This makes the collision-mesh group the
first priority.

## 3. Fix strategies (not implemented)

1. **Collision mesh (3 entries):** structurally the same as the covered self-contact traversals
   (dynamic slot claims, force/fixed-value float atomics, overlap records). The existing fast
   contact count/record/sort/apply path and the filtered serial reference should apply directly by
   adding the three entries to the kernelgen lists. Expected cost similar to self-contact.
2. **PDRD and translation locks (11 entries):** per-body/per-lock reductions (centroid, covariance,
   inertia, torque, row sums) with many contributions per address, partly inside the PCG loop.
   The generic replay is exact but too heavy to run per CG iteration. Better: a deterministic
   two-level reduction (per-element contributions written to a buffer, then a fixed-shape fold per
   body in index order), i.e. the same fixed-fold technique the PCG dot products already use. That
   changes the kernel bodies' reduction structure, so it needs its own validation against a serial
   reference.
3. **Cosserat (5 entries):** same as 2 inside the PCG; needs a framed-rod test scene first.
4. **Schwarz (1 entry):** only with a non-default preconditioner; document as unsupported in
   deterministic mode or give it the fixed-fold treatment.

Recommended order: collision mesh (high product relevance, low risk) → PDRD → translation locks →
Cosserat → Schwarz. Each with: serial reference path, fast path, two-reference + two-fast
benchmark, stage captures, NEON regression.

## 4. Cloud Bakes: how the deterministic mode could be used

What determinism would give Cloud Bakes:
- identical results for identical inputs on the same hardware class, so a cloud result can be
  re-verified, cached by input hash, and compared with a local preview;
- reproducible failure analysis (a crash or explosion can be replayed bit-exactly);
- confidence that splitting work across retries does not change the result — but see 4.1.

### 4.1 Checkpoint/recovery compatibility (measured)

NEON fast deterministic run, resumed from the solver's own checkpoint at frame 19 (`--load 19`,
`recipes/run-resume-determinism.py`):

- frames up to 19 are byte-identical (copied);
- **frames 20–29 differ from the uninterrupted run**: 0.3 mm at frame 20, growing to 7.7 mm at 29
  (68k–90k changed coordinates per frame);
- two resumes from the same checkpoint are byte-identical to each other;
- the uninterrupted run is byte-identical to the reference.

So resuming is deterministic but not equivalent to not interrupting: the checkpoint
(`state_<N>.bin.gz`: positions, previous positions, previous dt, dynamic CSR pattern, plus the rest
shape for plasticity) does not capture every quantity that influences the next steps. Candidates
to check: friction anchors/lagged contact state, linear-solve warm start, contact pair caches,
carried dynamic-matrix values, hydrodynamic transport state, adaptive/TOI controller state.
Consequence for Cloud Bakes: a bake that is preempted and resumed produces a different (but
reproducible) result than an uninterrupted one. Either the checkpoint must be extended until
resume is bit-exact (verifiable with this recipe), or the product must define the result as
"deterministic given the same resume schedule" and record checkpoint frames in the bake manifest.

### 4.2 Hardware-dependent reproducibility (assessment, not measured)

- All evidence is from one RTX 4070 SUPER (sm_89), one driver, one CUDA 12.8 toolchain. Nothing
  here certifies other GPUs.
- The backend ships cubins per architecture (sm_61…sm_120) built from compute_61 LTO IR. Different
  architectures run different machine code (instruction selection, FMA contraction), so
  cross-architecture bit-equality must not be expected. Within one architecture, the ordering
  mechanisms do not depend on SM count, occupancy or scheduling (fixed block sizes chosen from
  element counts, fixed fold shapes, order-defined replays), so same-architecture GPUs with the
  same binary and driver are expected to agree, but this is untested.
- Recommended for Cloud Bakes: pin GPU model/architecture, driver version and solver binary hashes
  per bake, record them in the result manifest, and treat results from a different hardware class
  as a different (re-bakeable) result rather than a reproduction. A first validation step is
  running the NEON and benchmark suites on two cloud GPUs of the same architecture and on one of a
  different architecture.

### 4.3 Other preconditions

- The fast path is still opt-in through environment variables; a Cloud Bake service would have to
  set them explicitly and record them.
- Scenes using the uncovered features (section 2) are not deterministic yet; a Cloud Bake could
  detect them from the session's bin files (`static_*`, `pdrd_*`, `translation_lock*`, framed rods,
  `precond = schwarz`) and label the result accordingly.
- Timing on shared or desktop GPUs varies strongly (NEON 54–102 s for identical code), so cost
  estimates should come from paired measurements on the target hardware.

## Evidence

`docs/validation/gaia026/reproducibility/audit-20261018/` (audit benchmark results, stage-capture
summary, resume test result). Raw outputs remain local under `.hydro-test-tmp/`.
