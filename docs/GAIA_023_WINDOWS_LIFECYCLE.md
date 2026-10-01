# Gaia 0.23 Windows lifecycle investigation

Date: 2026-09-30. Historical diagnostic snapshot below; see
[current lifecycle report](GAIA_023_LIFECYCLE_RESULTS.md) for the implemented
central ownership service, outside-Codex counterchecks and current real gates.
The statements below describe the earlier pre-fix state, not current code.

## Reproduced operation and observed state

The official build reaches `FixedScene.export_fixed`, then executes
`os.makedirs(project_root / "session")` at upstream `frontend/_scene_.py:933`.
Python raises `FileExistsError`, errno 17, WinError 183. The parent is the
fresh Cloth NeXt project alias; the child is absent. Before this failure,
the upload is complete and the server is BUILDING. No simulation checkpoint
or completed playback exists. Precreating `.cash` only moves the same failure
from cache creation to session creation and is not a fix.

The retained isolated project `clothnext_cc3a67d50737` was inspected with
`exists`, `lexists`, `lstat`, `resolve`, directory enumeration, and native
Windows API calls. Its canonical location is under the separate official
archive's `target/local/share/ppf-cts/git-unknown`; the target is
`dist/gaia-023-production-diagnostic/server-data/clothnext_cc3a67d50737`.
The alias is a directory symlink, tag `0xa000000c`, attributes 1040.
The target is an ordinary directory, attributes 16. Uploaded data, parameters,
hashes, upload ID, and the deliberately precreated `.cash` are present.
Neither `session` nor the failing diagnostic child exists, including by
`lexists` and `lstat` (the latter returns WinError 2).

The host Python and official bundled Python reproduce the failure. Calling
native `kernel32.CreateDirectoryW` directly through the alias returns 0 and
`GetLastError() == 183`. The correctly formed extended `\\?\` path behaves
the same, so the evidence does not support a MAX_PATH/normalization fix.
The same native operation on the resolved target succeeds.

A fresh unique junction, `clothnext_diag_3a93d59e45`, reproduces this without
any previous bake, recovery record, or child. `lstat` and `fsutil reparsepoint
query` confirm a real mount-point reparse tag `0xa0000003` and the expected
absolute substitute target. A fresh child fails through the junction but
succeeds at its resolved target. This is not an attempt to recreate an
already-existing link.

## Location matrix

All cases create unique junctions with the same CNX helper and an absent child.
They do not modify old projects or delete any content.

| Junction location | Target location | Child creation |
| --- | --- | --- |
| Repository `dist` | Repository `dist` | PASS |
| Repository `dist` | Isolated solver-audit AppData | PASS |
| Isolated solver-audit AppData | Repository `dist` | WinError 183; child absent |
| Isolated solver-audit AppData | Isolated solver-audit AppData | WinError 183; child absent |
| Fresh `%TEMP%` directory | Fresh `%TEMP%` directory | PASS |

The failure tracks the alias location, not the target location or stale state.
The repository and `%TEMP%` are writable execution roots; the solver-audit
location is outside those roots and was tested with approved escalated calls.
This correlation is **not proof** of an execution-environment cause. A matching
test in ordinary PowerShell outside Codex has been requested. Until that result
is available, attributing the failure to Windows generally, the solver, or a
sandbox would be speculation.

## Lifecycle and authority audit

Current new-bake path:

1. `SolverSession._run` prepares CNX recovery/project state where enabled.
2. `_start_owned_solver` creates the recovery-owned `server-data` directory,
   starts the owned server, and queries its canonical project root.
3. `official_scene_bridge.create_owned_project_link` removes a newly created
   empty ordinary canonical directory and creates a junction to the CNX target.
4. Upload writes official scene data through that project alias.
5. Official build creates cache/session/input directories through the alias.
6. CNX's official scene worker binds independent strengths through public APIs.
7. Simulation/fetch/frame sink provide the result and playback cache.
8. The session finally requests project deletion where recovery need not retain
   it, then stops and joins the owned process; later Blender cache cleanup uses
   its existing release-order and safe-delete architecture.

Current interrupted/recovery path:

1. CNX discovers identity-compatible metadata and verifies checkpoint ownership.
2. `recovery.owned_project_root` derives the authorized root from the metadata
   directory, not arbitrary paths supplied by metadata.
3. A new owned server is launched; the same link helper runs again.
4. A correctly resolving existing alias returns early from the helper.
5. Recovery reconciles server/disk checkpoints, rebinds authored parameters when
   required, and resumes. Preserved recovery projects skip normal deletion.

Link creation currently belongs to the CNX bridge helper. Link deletion is
implicitly delegated to TCMD deletion (`_delete_project`); the unchanged server
uses Rust `remove_dir_all` on the canonical project root. These paths have no
single explicit CNX link-ownership record or replacement transaction yet.
The worker's short-lived official `App.recover` text pointer is a different
alias: it is created exclusively, checks an existing pointer's exact target,
and removes only a pointer created by that invocation. It does not cause the
observed failure, which happens before that worker runs.

## Additional issues found, not the proved cause of the fresh-child failure

- `exists()` alone misses dangling aliases. Their creation collision currently
  reaches PowerShell rather than a structured, link-aware CNX error.
- An existing empty ordinary directory is removed without an explicit durable
  ownership marker; emptiness and a matching name are insufficient authority.
- There is no owned-stale-link replacement protocol or recovery transaction.
- The generic safe-delete helper resolves paths for containment. It must not
  be used to remove an alias itself, because resolving identifies its target.
- Server deletion and owned target/cache deletion must be tested separately;
  removal of an alias is not evidence that its target was cleaned up.

These findings require a centralized link lifecycle with link-aware inspection,
durable ownership, fail-closed unknown collisions, and explicit link-only
removal. They must not be conflated with the fresh `CreateDirectoryW` failure
or hidden by swallowing FileExistsError.

## Gates still open

Ordinary-shell countercheck, exact cause attribution, centralized product fix,
controlled collision/recovery failure injection, complete CUDA bake/playback/
rebake/cancel/recovery/cleanup, Blender restart, and real solver switching are
not passed in this phase. Baseline remains 2015 passed, 19 skipped, 3 deselected;
no production code or test was changed during this diagnostic phase.
No release is authorized or published.
