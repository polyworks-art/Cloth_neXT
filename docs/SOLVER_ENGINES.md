# Solver engines: AETHER and GAIA

Cloth NeXt offers exactly two solver engines in Preferences → Solver Engine.

| | AETHER | GAIA |
|---|---|---|
| What | The official, unmodified PPF Contact Solver by ST-Tech (ZOZO) | PolyWorks' separately maintained build of the same solver |
| Source | Official upstream GitHub releases pinned in `cloth_next/solver_compatibility.json` | A GAIA package (`gaia-build.json`) published by PolyWorks |
| Account | None | PolyWorks account with GAIA access |
| Installed by | Preferences → Manage → Official AETHER Releases | Preferences → Solver Engine → GAIA → Install GAIA |
| Redistributed by Cloth NeXt | Never | Only through the PolyWorks download service |

Cloth NeXt Classic itself is sold, licensed and updated through Superhive and
works with AETHER without any PolyWorks account.

## Identity and migration

An installation is GAIA only when its root carries a valid `gaia-build.json`
(`ClothNeXt.GaiaBuild.v1`: version, build id, source revision, base release,
backend, platform, protocol, and the SHA-256 of every patched file). Everything
else is AETHER (`cloth_next/updater/engines.py`).

Existing installations keep their files, registry entries and selection. The
historic upstream codenames (Gaia, Gaia 0.23, Gaia 0.26, Lumen, Velune) named
official ST-Tech releases, so they are shown as `AETHER · upstream <tag>`. An
external installation without GAIA identity is shown as
`AETHER · external, unverified build`, because Cloth NeXt cannot prove it is
unmodified. Nothing is re-selected by an add-on update.

The engine never changes by itself. Choosing an engine selects that engine's
newest healthy installation; if the engine is not installed the current one
stays selected and Preferences say so. Installing GAIA does not switch the
engine (except on a computer with no solver selected at all).

## GAIA verification

Before every Bake the patched GAIA files are checked against `gaia-build.json`;
a mismatch stops the Bake with a repair message instead of running anything
else. GAIA is CUDA-only (NVIDIA GPU, CUDA 12.8 capable driver).

## Reproducible Bake (GAIA only, opt-in)

`Reproducible Bake` runs GAIA's deterministic path (the `PPF_REPRO_*`
switches listed in `engines.REPRODUCIBLE_ENVIRONMENT`, including
`PPF_REPRO_WALK_SPMV`). It is off by default. Same scene, settings, GPU and
driver then give bit-identical results; it is slower (about 1x to 3x), and
scenes with collision meshes, rods, rigid bodies or translation locks are not
covered yet (47 CUDA entry points with float atomics still lack an ordering
path; see `docs/GAIA_FAST_DETERMINISTIC_SOLVER_20261018.md` on the GAIA
branch). Results are not certified across GPU architectures or drivers.
`PPF_REPRO_*` variables inherited from the environment never reach any solver
process.

## Installing GAIA from Preferences

All steps run on one background thread, never in a draw callback and never
during a Bake (`cloth_next/blender/gaia_account.py`):

1. Connect PolyWorks Account: browser device authorization; the token is stored
   in the OS vault shared with the Companion (DPAPI on Windows).
2. `GET /v1/account/gaia-access`; without access: Request Access or Redeem
   Invitation.
3. Install / Update / Repair: access is checked again, the compatible build is
   chosen from `GET /v1/account/downloads`, a fresh
   `POST /v1/solver/builds/{id}/download-session` is requested, and the file is
   downloaded with Range resume, backoff and cancellation
   (`cloth_next/polyworks/gaia_downloads.py`). The signed URL is never logged.
4. Size and SHA-256 are verified; the archive is inspected for traversal,
   links and reparse points, extracted to staging, every file is checked
   against `SHA256SUMS.txt`, the server version is probed, the real health check
   runs, and the build is moved into `versions/gaia-<version>-<rev>` and
   registered (`cloth_next/updater/gaia_install.py`). Any failure leaves all
   existing installations and the selection untouched; older GAIA builds stay
   installed for rollback.

The server stays the authority: visibility of GAIA in the engine menu is a
convenience, every download is authorised again by the API and the edge.

## Building a GAIA package

`tools/build_gaia_package.py` copies the CUDA runtime parts of the verified
official base release (server, Python frontend, embedded Python, CUDA runtime,
licenses) and adds the two rebuilt files (`target/cuda/release/ppf-contact-solver.exe`,
`bin/libsimbackend_cuda.dll`), the source patches, `GAIA-NOTICE.txt`,
`gaia-build.json` and `SHA256SUMS.txt`. Caches, sources, examples, ROCm,
ffmpeg and tools are excluded. Upstream is Apache-2.0; NVIDIA's CUDA runtime is
redistributed under the CUDA EULA as upstream does; the embedded Python packages
keep their own licenses.

## Tests

`tests/test_gaia_engine.py`, `tests/test_gaia_downloads.py`, and the real
Blender runs `tools/blender_gaia_install_e2e.py` (against
`tools/run_local_gaia_backend.py`) and `tools/blender_engine_bake_e2e.py`.
