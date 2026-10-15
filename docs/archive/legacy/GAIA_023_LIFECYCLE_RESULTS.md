# Gaia 0.23 Windows lifecycle: current results

Date: 2026-09-30. No release or external solver modification.

## 1–4. Root cause, filesystem operation, state and previous lifecycle

`os.makedirs(cache_root, exist_ok=True)` at official `build_worker.py:611`
returns WinError 183. Precreating `.cash` for diagnosis merely moves the same
failure to `os.makedirs(project_root / "session")` at `_scene_.py:933`.
BUILDING has not reached simulation. The failing child is absent by `lexists`
and `lstat` (WinError 2); its parent is a genuine directory symlink or junction
to an ordinary CNX workspace. Native `CreateDirectoryW` also returns 183 through
the alias, but succeeds at its resolved target, including with extended paths.

Unique-junction matrix: alias in repo → target in repo/AppData passes; alias in
AppData → target in repo/AppData fails; alias/target in fresh TEMP passes.
The user ran the identical AppData alias check in ordinary PowerShell outside
Codex: PASS. A production 32-frame CUDA bake there also passed. The fresh-child
failure is execution-context-dependent, not a preexisting filesystem collision.
The internal restriction mechanism has not been inspected and is not claimed.
An escalated tool call still failed at `.cash`; evidence is retained in
`dist/gaia-023-lifecycle-gates/aefb46acad60/lifecycle-report.json`.

Separately, the old lifecycle queried STATUS before linking, adopted an empty
ordinary directory without durable ownership, and delegated alias deletion to
TCMD while CNX owned target cleanup. Those safety defects were repaired, but
are not falsely claimed as the cause of the absent-child context failure.

## 5–7. Implementation, ownership protections and recovery

`cloth_next/ppf/project_links.py` is the single alias authority. Link-aware
inspection distinguishes absent/file/ordinary-directory/symlink/junction/other
reparse state. Even empty directories or correct links without ownership
metadata fail closed. Unowned targets are not adopted or deleted.

Authenticated-root markers validate project, canonical path, root, target,
phase and transaction nonce/paths. Linked/hardlinked markers and locks are
rejected. OS locks exclude concurrent operations. Owned replacement durably
records PREPARING, stages a replacement, renames the old alias to an owned
backup, promotes and validates the replacement, removes only the backup link,
then commits COMMITTED. Windows has no single atomic junction swap; interrupted
rename states are explicit and reentrant. Cleanup aborts partial transactions
without creating new aliases.

New bake ensures the alias before server startup/STATUS, then verifies the
server-reported path and actual backend. Build uses unchanged official APIs.
Finalization terminates and joins owned processes/readers first, removes the
owned alias without following it, and deletes the authenticated target using
existing `delete_owned`. Recovery is marked DELETED only after successful
cleanup. Preserved checkpoints keep their workspace and link. Recovery reuses
the same authority; its isolated owned health-probe link is cleaned after join.
Start Fresh likewise uses this authority before existing safe target/cache
cleanup. PC2/depsgraph release ordering is unchanged.

Structured errors retain raw exceptions and expected link/target, observed
type/target, record validity and actually proven ownership. No WinError 183 is
swallowed, no recovery/linking is disabled, and no solver code is patched.

## 8. Failure-injection results

Covered: correct reuse, absent creation, owned/unknown broken links, stale
replacement, wrong targets, ordinary files/directories (including empty ones),
unexpected reparse points, missing/corrupt/hardlinked metadata, competing locks,
interrupted backup/promotion, malicious transaction paths, repeated recovery
and cleanup, target cleanup failure after process join, preserving unknown
target data, and aborting cleanup without creating links. Actual Windows
junction/symlink removal preserves target contents.
Latest focused suite: **125 passed** (project links, bridge, solver sessions).

Real fixture corrections retain production validation: normal role assignment
creates the required CNX boundary modifier; explicit real solver resolution
avoids the user's persistent 0.22 selection without changing their registry.
Headless Steam Blender 5.2.2 validation using real 0.23 probe passes. The bridge
now reads both file-backed DATA and PARAM payloads; standalone worker imports
are also regression-tested.

## 9–13. Real lifecycle, switching, final counts, completion and blockers

Original AppData runtime, user-run outside Codex:

- `dist/gaia-023-lifecycle-outside/vertical_slice_report.json`: clean 32-frame
  CUDA bake PASS, alias and target cleaned, PC2 retained.
- `dist/gaia-023-lifecycle-gates/2e6120ce1ed7/lifecycle-report.json`: clean bake,
  real Steam Blender playback frames 1/2/32, PC2 release rename, second bake,
  overwrite/rebake PASS. Cancellation failed on the old fixture's missing
  boundary modifier before solver execution; that fixture is now corrected.

The full corrected harness is running with a fresh unchanged official archive
copy in the permitted temporary test root. This is isolated testing, not a
relocated installed solver or a shipped workaround. Verified archive SHA-256:
`fd3f43aa7e9849526b5113da558443a074039231ba700edfc953b6c83eb990d4`.
Its gates cover separate factory Blender processes, cancel/resume/cancel/fresh,
intra/cross/mixed sewing and real 0.23 → 0.22 → 0.23 switching via a temporary
registry. The persistent registry is checked byte-for-byte. Lumen is not
installed; its real switch gate is unavailable, with no additional download.

Final complete regression: **2045 passed, 19 skipped, 3 deselected** (156.66 s).
The existing skips were retained; no new skips mask lifecycle failures.
Compileall and `git diff --check` also pass. The 319 frontend/crates source
files in the fresh runtime match the original official runtime byte-for-byte;
both CUDA server and solver executable hashes match as well.

The final scoped verdict and remaining environment limitations are below.
No release is published.

## Additional real Blender finding: long recovery paths

The corrected cancellation fixture exposed a second, independent issue:
Blender 5.2.2 is not longPathAware. Its ordinary `open` fails with errno 2 for
an absent file in an existing directory when a recovery lock path reaches
274 characters; the same native Blender process succeeds with the Windows
extended-path operand. Once lock I/O was corrected, the partial PC2 and
checkpoint verification exposed the same restriction in the existing cache
and recovery code. These failures are retained in
`dist/gaia-023-lifecycle-gates/dd47c590323c` and were not bypassed by shortening
the fixture, disabling recovery, or accepting unverifiable states.

`core/filesystem_paths.py` separates unprefixed durable path identities from
extended Windows I/O operands for long paths. Alias metadata, PC2
write/resume/publish, checkpoint reads/verification and existing safe deletion
use that helper; containment is still resolved and authenticated before delete.
Native Blender tests now pass alias reuse/repeated removal and PC2 partial
resume/finalize/cleanup at paths over 260 characters.

Real long-path cancellation report `cancel-longpath-fixed4.json`: CANCELLED,
RESUMABLE, verified checkpoints 1/2, part-file preserved, owned junction
COMMITTED. Independent restarted Blender report `resume-longpath-fixed4.json`:
FINISHED, no upload, complete 24-frame PC2, alias absent, marker REMOVED, owned
target absent. Both reports are in the retained `dd47c590323c` run directory.

The complete harness in `dist/gaia-023-lifecycle-gates/6fad62fdef55` reconfirmed
all bake/playback/rebake gates but exposed a temporary-file boundary case: the
parent directory was shorter than the long-path threshold while the allocated
child was longer. The I/O helper now reserves room for generated temporary
names; native Blender verifies the 245-character-parent boundary explicitly.
The fresh complete harness is running again. It also fingerprints all source
and release binaries before/after for the isolated official 0.23 runtime and
the preexisting installed 0.22 runtime; it never applies source overlays.
The final full-suite rerun is green. The preceding rerun exposed an unrelated
asset test writing checked-in PNGs; that test now builds in a temporary output
root and retains comparisons against approved originals and a repeated build.

## Complete 0.23 lifecycle result and separate switch scope

`dist/gaia-023-lifecycle-gates/7d922e61ee9a/lifecycle-report.json` proves PASS
for native Blender long paths, clean CUDA bake, real playback/PC2 release,
second bake, rebake, cancel, restarted Blender resume, second cancel, Start
Fresh/new bake, and intra/cross/mixed sewing. The first temporary-registry
0.23 switch bake also passes. Source/binary fingerprints and the persistent
registry remain unchanged. No checkpoint/ownership/strength assertion was
relaxed to pass these gates.

That report correctly remains **FAIL**, not relabelled: the following installed
0.22 bake hits WinError 5 at its existing frontend `_decoder_.py:166`, creating
its normal cache child under AppData. This is another execution-context write
restriction; it is not a 0.23 recovery failure. No old frontend was patched.

The existing 0.22 runtime was copied to a fresh permitted TEMP test root,
excluding its user/runtime cache directories. All source and release binary
fingerprints match the original installation. `tools/run_gaia_solver_switches.py`
runs a separate scoped real 0.23 → 0.22 → 0.23 check through a temporary
registry, preserving the original failed report and fingerprinting both copies
and the original installed 0.22 before/after. Its report is
`dist/gaia-023-solver-switches/db7f173a89de/switch-report.json`: **PASS** for all
three real 32-frame bakes (0.23, 0.22, 0.23), with persistent registry and all
three runtime source/binary fingerprints unchanged.
This is an isolated test, not a permanent relocation or installation change.

## Final gate matrix and verdict

| Gate | Result | Evidence |
| --- | --- | --- |
| Native Blender long-path/temp-child boundary | PASS | `7d922e61ee9a/native-blender-long-path.log` |
| Real CUDA clean bake, second bake, rebake | PASS | `7d922e61ee9a/lifecycle-report.json` |
| Actual Blender playback and PC2 release | PASS | Same lifecycle report |
| Cancel, Blender restart/resume, repeat cancel, Start Fresh/new bake | PASS | Same report plus phases `6`–`9` JSON |
| Alias/target cleanup and repeat cleanup | PASS | All 11 lifecycle markers REMOVED; no alias/target leaks; native repeat-removal and failure-injection tests |
| Intra/cross/mixed sewing closure and free fall | PASS | Lifecycle sewing reports; all distances near 1 mm, no pinned seam regression |
| Independently authored strengths | PASS (previous verified phase retained) | Public setter/real CUDA proof in `GAIA_023_INVESTIGATION.md` and bridge regressions |
| 0.23 → 0.22 → 0.23 real bakes | PASS in byte-identical isolated copies | `db7f173a89de/switch-report.json` |
| Original installation, sources/binaries and persistent registry unchanged | PASS | Both reports' before/after hashes |
| Complete regression | PASS | 2045 passed, 19 preexisting skips, 3 deselected |
| Gaia → Lumen → Gaia | UNAVAILABLE | Lumen is not installed; no download |
| Full direct-AppData run from Codex | Environment-blocked | Retained Win183/Win5 failures; never relabelled PASS |

**Item 12 / integration verdict:** all available Windows functional integration
gates pass with unmodified, byte-identical isolated runtimes. The implementation
and tested Windows lifecycle are complete in that scope. This is **not an
unqualified installation-wide or global release-ready claim**: the direct
AppData full run remains blocked in the Codex execution context, Lumen is not
installed, and Linux was not part of this Windows lifecycle phase.

**Item 13 / remaining limitations:** there is no observed 0.23 lifecycle failure
in the permitted real Windows test environment. The remaining direct-AppData
restriction requires an actually different execution context, not another CNX
retry or a solver patch. Its internal restriction mechanism is uninspected.
The user already verified the original AppData alias creation and clean bake
outside Codex; the complete original-location sequence is not falsely claimed
as tested. No release, commit, push, persistent solver registration/selection
change, Reactive Materials work, or external solver modification was performed.
