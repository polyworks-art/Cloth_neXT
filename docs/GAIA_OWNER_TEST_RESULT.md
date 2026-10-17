# GAIA owner package validation — 17 October 2026

**Local owner evaluation works on Windows / Blender 5.2.2 LTS / RTX 4070 SUPER.**
This is an unpublished working snapshot, not production certification.
Manifest version remains **2.9.25**. No version, commit, push, tag, repository
index, workflow or release-policy change was made for this package.

## Candidate and installation

Candidate: `.owner-test-tmp/20261017/build-r6/cloth-next-owner-local.zip`.
Size: 40,379,739 bytes. SHA-256:
`5bb95f64a573e98bdacd687d99a3d90eac161a412b7e567443534e816a343669`.

Blender's official builder produced the ZIP; existing package validation and
solver-content scanning passed. The freshly compiled Companion also passed its
PE/content scan and actual authenticated socket, Bake, Welcome, What's New and
shutdown smoke. Its binary is identical across the tested candidate stages.
The ZIP contains no external solver/mesher binary, runtime, FLIP asset or scene.
A source-text scan found no development cache or developer-machine paths in it.

The extension was actually installed using Blender's `extension repo-add` and
`extension install-file -e` commands into fresh local repository `gaia_owner`.
Tests imported **bl_ext.gaia_owner.cloth_next from that installed package**,
without the source checkout on PYTHONPATH, the old experimental-manifest
environment variable, CARGO_TARGET_DIR, or a global DLL PATH modification.

The owner runtime manifest is an explicit preference, with a package-bound
dataset identity and server/worker/backend hashes plus a real native CUDA probe.
The verified backend directory is supplied only to the probe subprocess. The
normal process layout supplies runtime dependencies to the actual solver.
No global solver registry or official installation was changed. Test registries
and Blender preferences live in the separate profile directory.

Instructions and rollback: `GAIA_OWNER_TEST_BUILD.md`.

## Real Blender results

| Case | Observed result |
| --- | --- |
| Fresh ZIP installation and registration | PASS |
| Native CUDA runtime and bounds-capability probe | PASS |
| NEON 130–132 with real 133 lookahead | PASS; 96.60 s to completion |
| NEON 130–159 with real 160 lookahead | PASS; 458.88 s to FINISHED |
| Full PC2 import and Save/Reload | PASS, all original three cloth objects |
| New Blender process, short and full cache playback | PASS, all samples |
| Ordinary CUDA bake, hydrodynamics disabled | PASS; 30.96 s, original materials/attachments |
| Ordinary CPU bake in this external owner configuration | REJECTED by existing Material Zones capability guard; not certified |
| Changed original material | PASS negative test: unsupported-scene error before native solve |
| Cancel during EXPORTING | PASS; CANCELLED, no published PC2 |
| Missing FLIP cache | PASS negative test: explicit missing/not-baked error |
| Corrupted reconstruction copy | PASS negative test; changed frame-130 copy rejected before native solver startup |
| Real GPU memory exhaustion | NOT RUN; remains an unverified failure-mode test |

Times above stop at the controller's FINISHED state. Subsequent exhaustive cache
checks and fresh-process reload are additional wall time. Headless FLIP debug
drawing emits GPU-initialization warnings; these are not used as fluid data or
as evidence of a successful bake. No test is marked passing on the basis of
those warnings. Initial sandbox runs failed on Windows junction/socket access;
the real integration and link tests passed with ordinary host permissions.
Those failed attempts remain preserved.

Full playback compares all 30 samples of all three cloth objects against the
native output. Maximum native-to-PC2 error is **6.10343e-8 m**; maximum evaluated
playback deviation after a fresh Blender restart is **0 m**. Original three
materials/zones and all **186 attachments in four groups** were retained.

Independent reciprocal cloth-edge/collider-triangle and collider-edge/cloth-
triangle tests found **zero crossing segments across all 30 exported frames**.
The endpoint exclusion is 1 micrometre. This is not a proof of global inside/
outside classification or collision freedom between exported frames.
All exported cloth vertices remain inside SDF grid-centre support with a
minimum measured margin of **0.221753 m**. Native iterate guards remain active;
this offline margin is not an all-Newton-state measurement. No runtime processes
remain after the test suite and cancellation check.

## Reconstruction and physical evidence

This candidate uses the existing validated frozen FLIP 1.8.8 canonical grid-edge
surface results. The portable preparer performs the same occupancy/EDT transport
as the reference; it does not introduce another force implementation. No mesher
source or binary was packaged. General arbitrary-scene reconstruction and its
redistribution rights are not inferred from this local test.

All **22 short-run** and **130 full-run** binary transport files are bit-identical
to their previous isolated Blender references. The local data manifest binds
surface/bounds files, fluid-array identities, grids and timing. The actual array
checksums are verified when consumed. The scene gate binds source payload hashes
and the complete official conversion binding, including materials/attachments.
ZIP timestamps and cache-member relocation are not treated as physics changes.

The native signed-EDT voxel spacing remains **7.82636 mm**. Subcell features can
be lost; no claim of millimetre-perfect meshing is added. The original distinction
between sampled geometric distance, nearest-vertex distance and implicit-field
residual remains in `GAIA_RECONSTRUCTION_ACCEPTANCE_RESULT.md`.

Full owner run, 29 force outputs:

- Normalkraft: **91.0204–145.9594 N**; tangential coefficient remains zero.
- Wetted cloth area: **0.537453–0.672909 m²**.
- Maximum absolute difference from the prior full run: **3.26797 N** and
  **0.00230712 m²**. Fluid transport inputs are bit-identical; trajectories are
  subject to the already observed native repeatability limitation.
- Main garment point correspondence versus prior full run: **1.31144 mm RMS**,
  **26.97495 mm maximum** in world space. The existing 8 mm maximum screen is
  therefore **INVESTIGATE**, not PASS. The other cloth maxima are 0.134 mm and
  2.678 mm. This does not fix or waive repeatability.

Native telemetry consistency checks pass (finite values, wet area bounded by
total area, Ct=0 and drag-projection bound). The unchanged temporal screen flags
four transitions: 1→2, 20→21, 22→23 and 23→24, with a maximum load step of
20.49 N. These are the same transitions as the previous reference; both peak at
output 22 / source frame 152. The screen remains PENDING_REVIEW for owner visual
evaluation, rather than being silently relabelled a no-spike PASS.

One-way coupling, rho=1000 kg/m³, Cn=1, Ct=0, original material parameters and
native SDF/velocity support guards remain active. No legacy-flow double counting
was enabled. This is dynamic sheet drag, not a hydrostatic or FLIP-pressure-field
implementation. Visual production approval is not inferred from numerical tests;
the owner evaluation is the next artist review.

## Regressions, integrity and preserved results

- Focused owner/onboarding/Companion/cancellation/errors/distribution suite:
  **259 passed, 1 Windows-inapplicable POSIX-mode test skipped**.
- Broader GAIA/hydrodynamics/session/official bridge/project-link suite:
  **316 passed** with ordinary Windows junction permissions. These suites overlap;
  they are not added together as a unique test count.
- Existing native CPU/CUDA evidence is retained. No native code was changed or
  solver binary rebuilt for this owner package. The 19/20 self-contact result
  remains open. This report does not claim a fresh complete native CPU/CUDA suite.
- Experimental server, CUDA worker and backend SHA-256 values match the prior
  validation manifest. The frozen Blend SHA-256 remains
  `d0a7055fe124b807308308b78ad85f2a098ece209566e03d3c17f1d4fbaa2437`.
- All **93 original FLIP BOBJ/particle/velocity files** checked against preserved
  provenance remain unchanged. The current original Blend also matches its
  previous current-source hash. It already differed from the historical frozen
  snapshot before these tests; this investigation does not overwrite either.

Evidence is under `.owner-test-tmp/20261017/`: installed profiles and their logs,
`bake-r7`, `bake-long-r7`, fresh-process `verification.json` files, source-integrity
JSONs, transport/force/trajectory comparisons, collider audit, regression XML/log,
negative-test reports and build provenance. Prior `.hydro-test-tmp` results and
the earlier acceptance report were not replaced.
All 315 non-generated staged source files also match the current add-on source;
the tested ZIP was not silently rebuilt after its integration tests.

## Remaining blockers and publication

The local package is usable for the strictly scoped CUDA owner evaluation.
Production/release safeguards remain **NO-GO**: self-contact parity 19/20,
repeatability above 8 mm, NEON-only reconstruction, interframe/additional-scene
coverage, real GPU-memory-exhaustion handling and the rejected external CPU
material-zone combination. No workaround removes materials or weakens guards.

This report describes the preserved local 2.9.25 candidate. The owner subsequently
clarified that the existing private GitHub channel is separate from Superhive
customer delivery and approved **2.9.26** for that channel. The earlier routing
interpretation was incorrect. Version 2.9.26 uses the existing exact-SHA Dev
workflow and unchanged policy, with version-bound owner gate metadata. Its
publication outcome must be verified separately; the historical dirty snapshot
above is not itself a published source commit. An obscure GitHub Pages URL does
not provide private access control.

Recommended next steps: owner visual review using the included instructions;
resolve native repeatability/self-contact blockers; certify CPU material-zone
capability separately; add a controlled GPU-memory-failure integration test;
then verify the approved private-channel build against its exact source commit.
