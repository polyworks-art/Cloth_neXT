# Extension packaging

The extension root is `cloth_next/`. Every development and release package is
solver-free: Cloth NeXt never copies the external PPF Contact Solver into an
extension archive. `tools/scan_release_artifact.py` rejects solver material.

Development build (pure-Python fallback when Blender is unavailable):

```powershell
python tools\build_extension.py `
  --output dist\cloth_next-development.zip
```

The default output name is derived from `cloth_next/blender_manifest.toml` when
`--output` is omitted.

Release build with Blender's official extension tooling:

```powershell
python tools\build_extension.py `
  --blender "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" `
  --output dist\cloth_next-<version>-windows-x64.zip
```

Both modes validate the ZIP layout and scan the finished artifact. Release CI
additionally builds and stages the Cloth NeXt-owned Windows Bake companion at
`bin/cloth-next-bake.exe`; that executable is UI software, not the solver.
The corresponding Linux x86_64 candidate contains `bin/cloth-next-bake` with
its executable mode retained. Platform candidates never contain both binaries.
The package also contains validated offline onboarding resources under
`resources/onboarding/`: the manifest-version `whats_new/<version>.json` plus the
shared hero and icon pool. Welcome is invariant Companion content and therefore has
no release JSON. Blender passes
that installed resource directory through `--content-root`; release-specific copy
and icon choices are not compiled into the Companion EXE. Release policy rejects invalid UTF-8 or
JSON, version mismatches, missing assets, absolute paths, unsafe URLs, missing
Companion modes, or an archive without these resources for Dev, Beta, and Stable.

The installed extension directory is treated as read-only. Managed solver files,
downloads, logs, and runtime state live under `%LOCALAPPDATA%\ClothNeXt\solver\`.
On Linux, persistent solver data lives under
`$XDG_DATA_HOME/ClothNeXt/solver/`, or `~/.local/share/ClothNeXt/solver/` when
`XDG_DATA_HOME` is unset.

## Linux local checks

On native Linux (or WSL2 with WSLg), install Python 3.11, Tk, Xvfb, and the
build requirements, then run:

```bash
pytest -m "not integration and not built_artifact"
python companion/build_companion.py
xvfb-run -a python tools/verify_companion.py
python tools/stage_companion.py "companion/dist/Cloth NeXt Bake"
python tools/build_extension.py --output dist/cloth_next-2.7.8-linux-x64.zip
python tools/validate_extension.py dist/cloth_next-2.7.8-linux-x64.zip --phase packaged
```

Use the version already present in `blender_manifest.toml` when it changes;
these commands do not authorize a version bump. WSLg is useful for IPC and
window-layout checks but does not reproduce every native desktop compositor,
GPU driver, file-permission, or session-lifecycle behavior. A native Linux
desktop remains the final visual and real-solver acceptance environment.
See [Solver distribution](SOLVER_DISTRIBUTION.md) and the mandatory
[Release policy](RELEASE_POLICY.md).
