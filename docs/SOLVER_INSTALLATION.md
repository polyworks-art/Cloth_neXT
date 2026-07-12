# Solver installation

The PPF Contact Solver is external software by ST Tech / ZOZO. Cloth NeXt does
not include it; the add-on preferences install or select it separately. See
[SOLVER_DISTRIBUTION.md](SOLVER_DISTRIBUTION.md) and
[RELEASE_POLICY.md](RELEASE_POLICY.md) section 13.

## Installation modes

| Mode | Origin | Cloth NeXt may | Cloth NeXt must never |
|---|---|---|---|
| Managed | Downloaded from the official source after explicit confirmation | health-check, install versions side by side, switch/repair/remove managed versions | install into the extension folder |
| External installation | Selected by the user | validate, probe versions, health-check, start and stop only self-started processes | modify, delete, or update the files |
| External server | Already-running PPF server | connect, query | stop, restart, update, delete, or take over the process |

## Managed installation location (Windows)

```text
%LOCALAPPDATA%\ClothNeXt\solver\
├─ versions\<official-version>\   # side-by-side installations
├─ current.json                   # active version pointer
├─ downloads\                     # verified archives
├─ staging\                       # extraction sandbox
└─ logs\
```

Never used: the Blender extension root, the Cloth NeXt repository, Program
Files, the current working directory, or a temp directory. Add-on updates
therefore never delete the solver, and solver updates never touch the add-on.

## Download pipeline

User confirmation → official HTTPS download (redirects restricted to official
GitHub hosts, size-limited, progress, cancellable) → SHA-256 verification →
safe archive inspection (no traversal, absolute paths, symlinks, or reparse
points) → staging extraction → executable discovery (only `ppf-cts-server.exe`
is ever probed) → version/protocol/schema probe → real health check → atomic
side-by-side publication → activation. Every failure preserves the previously
active installation. Downloads never start automatically — not on add-on
enable, file open, simulation start, Blender start, or in the background.

The confirmation dialog always states: the software is external, who develops
it, the official source, the exact version, the download size, and the
installation location.

## Solver updates

Only versions listed in `cloth_next/solver_compatibility.json` are offered.
Unknown upstream versions are never assumed compatible. Updates install side
by side and switch the active version only after the health check passes; no
update runs while the solver, a simulation, a build, a frame fetch, or a cache
write is active.

## Development mode

Set `CLOTH_NEXT_PPF_EXECUTABLE` to a pinned local binary for integration
tests; without it they skip cleanly. Local solver trees stay Git-ignored, and
release CI still verifies the extension artifact is solver-free.
