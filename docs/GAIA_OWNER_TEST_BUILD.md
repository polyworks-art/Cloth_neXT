# Local GAIA owner evaluation

Status: unpublished working snapshot, manifest version **2.9.25 unchanged**.
This is not a production release or approval to reuse that version in a feed.
No customer index, release policy, tag, commit or GitHub Release is changed.

## Package and local data

The current candidate is under `.owner-test-tmp/20261017/build-r6/`:

- `cloth-next-owner-local.zip`: extension, freshly built own Companion,
  onboarding and signed-license client code; no external solver/mesher binaries,
  FLIP assets, Blender scenes or simulation caches.
- `build-provenance.json`: exact staged file hashes, ZIP hash and Git HEAD.
  The source is a dirty working snapshot, **not an exact tested source commit**.
- `local-runtime/owner-runtime.json`: explicit references to the separate,
  existing experimental runtime and external preparation dependencies.
- `local-runtime/neon-dataset.json`, `surfaces/`, `bounds/`: local private
  owner data, bound to the extension marker by SHA-256; never publish these.

Keep the entire build folder. Its local runtime manifest currently references
the already isolated experimental runtime; it does not copy over or change the
official installation. Moving that runtime requires updating its local manifest
paths and retaining the original server/worker/backend hashes.

The extension has no downloader for experimental binaries and does not bundle
the FLIP mesher. Only the previously validated, frozen surface results are used.
No FLIP mesher source/binary redistribution permission is inferred by this build.

## Manual installation in Blender

1. Back up Blender preferences and note the currently selected solver. Disable
   the existing Cloth NeXt add-on before enabling this candidate: both register
   the same Blender properties/operators and must not run together.
2. In Preferences → Get Extensions, create/select a **local** repository and
   use Install from Disk for the ZIP. Do not add a customer or guessed remote URL.
3. In Cloth NeXt preferences, GAIA Owner Evaluation, choose
   `local-runtime/owner-runtime.json` from this build folder.
4. Use the existing external solver chooser to register the runtime root named
   in that JSON. Select that separate installation under Active Solver and CUDA.
   This does not replace the official runtime. Save preferences explicitly.
5. Open a working copy of the frozen NEON reference scene
   `.hydro-test-tmp/blender-e2e-20261016/SW04_NEON-copy.blend`. Do not save over
   the original source or this preserved baseline. Its FLIP/DNA dependencies must
   be installed. Set the domain's cache path to the existing NEON FLIP cache
   through FLIP's normal cache control; keep that cache read-only.
6. For all three cloth objects retain original materials, attachments and
   animation. Choose **130–132** for the short test or **130–159** for the full
   evaluation. Use a new writable Cloth NeXt output cache directory.
7. Enable Fluid Interaction and Hydrodynamic Pressure, select the NEON domain,
   and use the normal Bake operator. The normal FLIP preparation creates the
   velocity container. Owner preparation verifies its actual array identities,
   reconstruction/bounds hashes and real next-frame lookahead before native use.
8. Save a result copy, restart Blender and verify cached playback. Disable
   Hydrodynamic Pressure for ordinary cloth tests; no account login is required.

Only the original NEON source configuration and these exact validated ranges
are admitted. Material, geometry, attachment, collider, timing or frame-range
changes can cause an actionable unsupported-scene error. This is deliberate;
the package does not promise general FLIP reconstruction. It neither fills
arbitrary holes nor removes disconnected water bodies.

## Physics and numerical scope

The transport remains native CUDA, one-way, rho=1000 kg/m³, Cn=1, Ct=0, with
native velocity support and SDF bounds guards active. It uses the validated
canonical grid-edge stitched FLIP 1.8.8 surfaces and 7.82636 mm voxel transport.
Small subcell structures can disappear in voxelization. This is sheet drag,
not a FLIP pressure-field or hydrostatic-pressure solver. CPU remains available
for ordinary cloth where supported; owner hydrodynamics requires CUDA.

The NEON dry test using this external runtime's CPU selection is rejected by
the existing Material Zones capability guard. Do not remove zones or relabel
the runtime as managed to bypass it. This owner configuration is validated on
CUDA; ordinary CPU use with supported managed installations retains its existing
compatibility rules and is not certified by the NEON owner test.

Known release blockers remain: native CPU/CUDA self-contact parity 19/20;
repeatability can exceed the previous 8 mm maximum screen; only NEON has been
validated; interframe collisions and additional production scenes need testing.
The earlier 30-frame results remain in
`GAIA_RECONSTRUCTION_ACCEPTANCE_RESULT.md` and its linked evidence. They do not
automatically certify a freshly installed package.

## Rebuilding and verification

Build the Companion with `companion/build_companion.py --output-root <new-dir>`.
Use `tools/build_gaia_owner.py --help` for required explicit paths to that EXE,
Blender, the frozen surface manifest, reference fluid container, isolated runtime
manifest and successful reference run(s). It creates a new output folder, uses
Blender's official extension builder and existing validators/scanners, and never
publishes. Do not point `--output` into a working runtime or previous result.

`tools/run_gaia_owner_install.py` installs through Blender's actual extension
manager into a fresh profile, imports `bl_ext.gaia_owner.cloth_next` from the
installed ZIP, then exercises the regular Bake operator. It removes the previous
experimental environment and developer PYTHONPATH. Windows integration needs
normal local socket and junction permissions; a restricted sandbox can prevent
these independently of the package. `tools/blender_verify_neon_hydrodynamic_bake.py`
supports `--addon-module bl_ext.gaia_owner.cloth_next` for a fresh-process replay.

See `GAIA_OWNER_TEST_RESULT.md` for observed results and untested cases. Do not
mark an unrun fault-injection or real GPU-memory-exhaustion case as passing.

## Private-channel publication

The owner clarified that the configured private GitHub channel is separate from
customer delivery through Superhive, and explicitly approved **2.9.26**. The
initial analysis incorrectly conflated those destinations. The existing
`publish-dev.yml` route is the intended private test channel; no new distribution
policy or replacement repository is required. The secret destination is never
copied into source, documentation, or packages.

The local 2.9.25 candidate above remains preserved as historical validation.
For 2.9.26, version-bound `gaia_owner_build.json` supplies only the approved
dataset digest to `prepare_dev_build.py`; the Dev snapshot receives the same
explicit owner gate. No local runtime path or source data is uploaded. The
normal official index generator and all existing publication checks still apply.

Publication requires an exact tested source commit and successful workflow.
No GitHub Release or tag is created for this Dev version. Pages hosting does not
provide private access control merely because the channel URL is unadvertised.

## Rollback

Finish or cancel the owner Bake first. Disable/uninstall the owner extension
from the local repository, re-enable/reinstall the previous official package,
and select the previous solver in Preferences. Restore backed-up preferences
if needed. Keep owner PC2/cache/results and all prior regression evidence.
The official binaries and customer distribution are not overwritten by this
workflow. Remove local owner data only after deciding it is no longer needed.
