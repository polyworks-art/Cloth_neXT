# Solver bootstrap

Phase 2.5 imports an already obtained official/local Windows runtime explicitly. It
never guesses a download URL and never downloads during tests.

```powershell
python tools\bootstrap_ppf_solver.py --archive C:\Downloads\official-win64.zip `
  --source-url https://github.com/st-tech/ppf-contact-solver/releases/download/<tag>/<asset>
```

`--directory` and `--executable` are also accepted. The executable form imports its
whole parent tree because the server is not a standalone EXE. Default target is
`solver/windows-x86_64/`; `--target` overrides it.

The importer extracts/copies to `solver/.staging-<uuid>`, rejects traversal and
symlinks, requires exactly one server executable and at least one LICENSE/NOTICE,
preserves runtime files, runs `--version`, requires package `0.1.0`, protocol `0.11`,
schema `1`, then starts the real server on an ephemeral port. Only after
`SERVER_READY` plus a compatible TCMD status does it create `SOURCE.json` and atomically
exchange the target. Failure removes staging; an old target is restored from its
temporary backup.

Mutable progress/log state uses a temporary `ClothNeXt-*` directory outside source and
extension roots. Absolute local source paths are recorded only as `redacted`.

The currently used official archive omitted a root license file. For the local import,
the unchanged Apache-2.0 `LICENSE` from the official repository checkout was added
before bootstrap. The tool correctly refuses an archive/directory with no license.

