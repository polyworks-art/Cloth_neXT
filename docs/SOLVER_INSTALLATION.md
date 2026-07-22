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
├─ versions\<official-release-tag>\   # side-by-side installations
├─ current.json                       # active installation identity
├─ downloads\                         # verified archives
├─ staging\                           # extraction sandbox
└─ logs\
```

New managed installations are stored under the immutable official release tag
(e.g. `versions/2026-07-13-21-05/`), never under the internal solver package
version alone: multiple official releases may report the same internal package
version and must install side by side. The release id is strictly validated
(no separators, no traversal). Legacy installations under
`versions/<package-version>/` (e.g. `versions/0.1.0/`) remain valid and
startable and are never touched by an update.

## Installation identity (`current.json`)

`current.json` metadata version 2 records the full immutable release identity:

```json
{
  "metadata_version": 2,
  "installation_id": "2026-07-13-21-05",
  "official_release_tag": "2026-07-13-21-05",
  "official_asset_name": "ppf-contact-solver-2026-07-13-21-05-win64.zip",
  "asset_sha256": "<64 lowercase hex>",
  "solver_package_version": "0.1.0",
  "executable": "target/release/ppf-cts-server.exe",
  "activated_at": "<ISO-8601 UTC>"
}
```

The legacy version-1 format (`active_version` + `executable`) stays readable;
such installations keep working, are never destructively rewritten just by
reading them, and — because their exact official release identity is unknown —
are offered the current manifest-pinned release as a compatible update. The
new format is written only after a successful installation and health check.
Corrupted or tampered metadata is never trusted and leads to the repair flow.

## Update detection and the preferences notice

Whether a solver update exists is decided by one central, pure comparison
(`cloth_next/updater/update_check.py`): a managed installation is outdated
when its official release tag or asset SHA-256 differs from the bundled
`solver_compatibility.json` entry, or when the release identity is unknown
(legacy metadata). The internal solver package version alone never decides it;
package, protocol, and schema remain mandatory checks of the actually
downloaded executable. The same release tag with a different manifest hash is
logged and treated as an integrity/manifest problem, never as a silent release
switch.

The comparison runs locally when the preferences are drawn — no network
request, no thread, no process start. When an update is available for a
managed installation, the PPF Contact Solver section shows a red alert box
("Solver Update Available") naming the installed and the available release,
with an "Install Compatible Solver Update" button that opens the existing
confirmation dialog. Downloads never start automatically. External
installations are never modified and never reported as outdated when their
exact official release cannot be determined. Solver updates and Cloth NeXt
add-on updates remain separate lifecycles.

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
