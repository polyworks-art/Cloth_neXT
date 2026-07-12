# Local PPF Windows solver

This directory is the development location for the official Windows x86-64 PPF
redistributable. Binary/runtime contents and generated `SOURCE.json` are intentionally
ignored by Git. `.gitkeep` and this guide remain tracked.

Bootstrap an already downloaded official archive or directory explicitly:

```powershell
python tools\bootstrap_ppf_solver.py --archive C:\path\official-win64.zip `
  --source-url https://github.com/st-tech/ppf-contact-solver/releases/download/<tag>/<asset>
```

The importer rejects traversal, symlinks, missing licenses, version mismatches, and a
failed real health check. It preserves the complete runtime tree, creates `SOURCE.json`,
then atomically replaces this directory. Never copy only `ppf-cts-server.exe`.

Current locally validated source is documented in `docs/SOLVER_DISTRIBUTION.md`.

