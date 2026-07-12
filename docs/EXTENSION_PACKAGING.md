# Extension packaging

The extension root remains `cloth_next/`; the repository solver is copied into the
archive as `solver/windows-x86_64/` only for an explicit release build.

Development build:

```powershell
python tools\build_extension.py --without-solver `
  --output dist\cloth_next-development.zip
```

This succeeds without any solver. Runtime resolution can use an external or repository
installation and reports “Solver not bundled” at the product layer.

Release build:

```powershell
python tools\build_extension.py --with-solver `
  --output dist\cloth_next-release-with-solver.zip
```

It fails unless executable, `SOURCE.json`, at least one license file, protocol `0.11`,
schema `1`, and `health_check: passed` are present. The current real build succeeded:

- archive size: 452,882,631 bytes;
- archive SHA-256: `5fb18dfdc1ac4b1afe2f234d90b1751757ada0e837ee27fc6c4b98731024a107`;
- manifest and `__init__.py` validated at ZIP root;
- bundled server located at `solver/windows-x86_64/ppf-cts-server.exe`.

The installed extension directory is treated read-only. Solver execution is allowed,
but progress/log/runtime state is injected outside it. Bootstrap never modifies an
installed extension; replacement happens through a new package installation.

