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

The installed extension directory is treated as read-only. Managed solver files,
downloads, logs, and runtime state live under `%LOCALAPPDATA%\ClothNeXt\solver\`.
See [Solver distribution](SOLVER_DISTRIBUTION.md) and the mandatory
[Release policy](RELEASE_POLICY.md).
