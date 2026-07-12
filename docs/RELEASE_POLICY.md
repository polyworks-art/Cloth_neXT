# Cloth NeXt Release Policy

> **Mandatory project policy**
>
> This document is the authoritative release and update policy for Cloth NeXt.
> Any developer, automation, AI coding assistant, or CI workflow modifying versions,
> tags, releases, extension packages, update channels, solver compatibility metadata,
> or release artifacts must read and follow this document before making changes.
>
> If another instruction conflicts with this policy, stop the release process and
> request an explicit policy update. Do not silently bypass, reinterpret, or weaken
> these rules.

## 1. Purpose and Authority

This policy governs how Cloth NeXt is versioned, packaged, released, published to
Blender extension repositories, and updated, and how the external PPF Contact Solver
is installed and updated separately. It is enforced technically by
`tools/validate_release_policy.py`, `tools/scan_release_artifact.py`, the test suite,
and `.github/workflows/release.yml`, and organizationally by this document.

Cloth NeXt publishes exclusively: the Cloth NeXt add-on code, its own correctly
declared Python dependencies, its own release metadata, a solver compatibility
manifest, and the Cloth NeXt-owned Windows Bake companion built from the tagged
repository source by the release workflow. Cloth NeXt never publishes PPF binaries,
PPF runtime files, or PPF archives.

The sole approved executable is `bin/cloth-next-bake.exe` inside the Windows
extension ZIP. It is validated by `companion_manifest.json` (version, platform,
size, and SHA-256), is never a separate release asset, and does not authorize any
other executable, third-party binary, solver DLL, archive, or runtime.

## 2. Version Source of Truth

The canonical Cloth NeXt version lives exclusively in:

```text
cloth_next/blender_manifest.toml   →   version = "X.Y.Z[-prerelease]"
```

For every release, the following must be identical (modulo the leading `v` on tags):

```text
Manifest version
Git tag                      v<version>
GitHub release               v<version>
Extension ZIP file name      cloth_next-<version>-windows-x64.zip
release-manifest.json        cloth_next_version
solver_compatibility.json    cloth_next_version
Blender repository index.json entry version
```

Any mismatch aborts the release. No component may derive or invent its own version.

## 3. Semantic Versioning Rules

Cloth NeXt uses Semantic Versioning. Allowed forms:

```text
MAJOR.MINOR.PATCH               e.g. 0.2.0, 0.2.1, 1.0.0
MAJOR.MINOR.PATCH-beta.N        e.g. 0.3.0-beta.1
MAJOR.MINOR.PATCH-rc.N          e.g. 0.3.0-rc.1
MAJOR.MINOR.PATCH-dev.N         e.g. 0.2.0-dev.1
```

No other prerelease identifiers, no build metadata, no leading zeros. Versions are
never reused and never decreased. An AI assistant or automation must never choose a
version on its own; the version is supplied by the human release manager.

## 4. Release Channels

Three channels exist: `stable`, `beta`, and `dev`.

- Stable accepts only plain `MAJOR.MINOR.PATCH` tags (`v0.2.0`, `v1.0.0`).
- Beta accepts only prerelease tags (`v0.3.0-beta.1`, `v0.3.0-rc.1`).
- Prerelease versions must never appear in the stable repository.
- A stable release requires a previously successful beta end-to-end test.
- New release infrastructure is exercised in the beta channel first.
- Automation never promotes beta to stable on its own.
- Dev accepts only `MAJOR.MINOR.PATCH-dev.N`, is never selected automatically,
  and is published only by its confirmation-gated manual workflow.
- Dev creates no tag or GitHub Release, retains at most five immutable ZIPs,
  and never skips packaging, secret, companion-integrity, or solver scans.

Generated paths: `site/stable/`, `site/beta/`, and `site/dev/`, each with its own
Blender-generated `index.json`. See `docs/UPDATE_CHANNELS.md`.

## 5. Required Version Consistency

`tools/validate_release_policy.py` enforces, before the build, after the build, and
before publication:

- tag version equals manifest version,
- ZIP file name equals `cloth_next-<version>-windows-x64.zip`,
- the manifest inside the ZIP carries the same version,
- `release-manifest.json` carries the same version, tag, and ZIP hash,
- `SHA256SUMS.txt` matches the actual ZIP,
- channel rules of section 4 hold,
- `solver_compatibility.json` is valid and matches the manifest version.

## 6. External PPF Solver Policy

The PPF Contact Solver is external software developed and distributed by
ST Tech / ZOZO (`st-tech/ppf-contact-solver`). It is not part of Cloth NeXt.

Cloth NeXt releases never contain, mirror, repackage, proxy, or redistribute
the PPF Contact Solver executable or its runtime bundle.

Cloth NeXt may provide an installer that downloads a verified compatible solver
directly from an official upstream source after explicit user confirmation.

Concretely, Cloth NeXt must never:

- commit the PPF solver to the Cloth NeXt Git repository,
- version it through Git LFS,
- include it in the Blender extension ZIP,
- upload it as a Cloth NeXt GitHub release asset,
- mirror it through Cloth NeXt GitHub Pages,
- host it under any Cloth NeXt download URL,
- present it as Cloth NeXt's own software,
- repackage or redistribute it together with the add-on.

## 7. Solver Compatibility Manifest

`cloth_next/solver_compatibility.json` is metadata only. It pins, per platform, the
exact official solver release Cloth NeXt has verified: repository owner `st-tech`,
repository `ppf-contact-solver`, an immutable official release tag, asset name, asset
URL, download size, SHA-256, protocol version, and schema version.

Rules (`docs/SOLVER_COMPATIBILITY_MANIFEST.md` has the full schema):

- only official `st-tech/ppf-contact-solver` release assets are allowed,
- no Cloth NeXt mirrors, unofficial forks, arbitrary CI artifacts, invented URLs,
  local paths, or mutable `latest` references,
- SHA-256, protocol version, and schema version are mandatory,
- unknown solver versions are never assumed compatible,
- a new upstream version may only be offered after it has been tested and added to
  the manifest through a reviewed change.

If no verified official URL or checksum is available, automatic download stays
disabled and the UI offers only "Select Existing Installation" and
"Open Official Download Page". Never invent a source to complete the installer.

## 8. Required Release Checks

A release must pass, in order: policy validator (pre-build), unit tests, integration
tests (skipped cleanly when no local solver is configured), extension structure
validation, solver compatibility manifest validation, extension build through the
official Blender extension tooling, artifact scan for forbidden solver files, ZIP
validation, policy validator (post-build), and policy validator (pre-publish).
A failing check aborts the release; nothing is published.

## 9. Git Tag and GitHub Release Rules

- Releases are triggered by pushing an annotated tag `v<version>` matching the manifest.
- Existing tags and existing releases are never replaced, moved, or force-pushed.
- The GitHub release is created as a draft first; it is published only after every
  build, validation, repository-generation, and Pages step succeeded.
- Beta releases are marked as GitHub prereleases.
- Release assets are exactly: the extension ZIP, `release-manifest.json`,
  `SHA256SUMS.txt`. The extension ZIP may contain only the policy-approved Cloth
  NeXt Bake companion executable. No solver binaries, archives, or runtime files, ever.

## 10. Blender Extension Repository

A GitHub release alone is not a Blender update mechanism. Each channel provides a
Blender-compatible remote extension repository whose `index.json` is generated by the
official Blender tooling (`blender --command extension server-generate`). No custom
`index.json` schema is ever invented. The ZIP inside the repository must be
byte-identical (SHA-256 verified) to the tested GitHub release asset.

## 11. GitHub Pages Deployment

GitHub Pages serves the `gh-pages` branch. The release workflow updates only the
channel directory of the release being published (`stable/` or `beta/`), never the
other channel, and never deletes previously published versions. Pages never hosts
solver files. URLs are documented in `docs/UPDATE_CHANNELS.md`.

## 12. Add-on Update Behavior

Cloth NeXt updates are installed exclusively through Blender's own extension update
mechanism against the channel repositories. The add-on never replaces its own loaded
extension directory. An add-on update must not start while the solver is starting or
running, a scene transfer, build, simulation, frame fetch, or cache write is active,
or a cancellation is in progress (see `cloth_next/updater/addon_update_guard.py`).
Before updating: finish or cancel the active solve, stop the owned solver process,
confirm process exit, close handles, then use the Blender extension update, and
restart Blender if required. An add-on update never deletes or modifies an installed
solver.

## 13. Solver Installation and Update Behavior

The solver is installed separately, after explicit user confirmation, into a
user-writable location outside the Blender extension directory
(`%LOCALAPPDATA%/ClothNeXt/solver/` on Windows). Three modes exist:

- **Managed installation** — Cloth NeXt downloaded a manifest-pinned official
  release after confirmation; Cloth NeXt may health-check, install compatible
  versions side by side, switch the active managed version, repair, and remove it.
- **External installation** — the user selected an existing installation; Cloth NeXt
  may validate, probe versions, health-check, and start/stop only processes it
  started itself. It never modifies, deletes, or updates external files.
- **External server** — the user connects to a running PPF server; Cloth NeXt never
  stops, restarts, updates, deletes, or takes over that process.

A solver update installs only manifest-listed versions, side by side, and switches
the active version only after SHA-256, protocol, schema, and a real health check
succeed. The previous active version is preserved until then. No in-place overwrite
of an active installation. Solver updates never modify the Cloth NeXt add-on.

## 14. Release Artifact Immutability

Published release assets, tags, and repository ZIPs are immutable. A republish with
different bytes under the same version is forbidden; fixing a bad release requires a
new version. The workflow aborts if a tag, release, or same-named channel ZIP with a
different hash already exists.

## 15. Failed Release Handling

If any step fails: the draft release is not published, GitHub Pages is not updated,
the existing repository content stays unchanged, and existing artifacts are not
replaced. Diagnose, fix, and release a new version (or the same version only if
nothing was ever published under it — a draft may be deleted).

## 16. Mandatory Preflight-Before-Tag Procedure

Before creating a release tag, the release manager must commit and push the intended
release state, run `release-preflight` against that exact commit SHA, inspect the
uploaded `cloth-next-release-candidate`, and verify every source, Windows companion,
Blender, package, scanner, and policy gate. Only then may an annotated tag be created
at that same commit. The tagged workflow rejects a successful preflight from another
commit or manifest version. `python tools/check_release_preflight.py --commit <sha>
--version <version>` provides the same mandatory check locally when GitHub CLI is
authenticated. The preflight never tags, publishes, changes Pages, or modifies the
Stable or Beta repositories.

## 17. Codex Release Procedure

When instructed "Release Cloth NeXt \<version\>", an AI assistant must:

1. read this policy,
2. verify repository, branch, and a clean working tree,
3. validate the requested version (section 3) — never choose one itself,
4. determine the channel (section 4),
5. verify no tag or release for it exists,
6. update the manifest version,
7. update the changelog,
8. validate `solver_compatibility.json`,
9. run all tests,
10. build the extension locally,
11. verify no PPF solver is contained,
12. run the release policy validator,
13. commit, push the branch,
14. create and push tag `v<version>`,
15. let the GitHub Action produce the release — never a parallel manual one,
16. monitor the workflow, verify the release and the Pages repository,
17. report success only after everything completed.

It must never pick a version, skip tests, create a stable release without a passed
beta end-to-end test, replace an existing tag, or upload PPF binaries.

## 18. Prohibited Actions

- Any inclusion, mirroring, proxying, or redistribution of PPF solver files
  (section 6).
- Blindly installing the newest upstream PPF release or auto-accepting an unknown
  solver version as compatible.
- Downloading without explicit user confirmation; starting unverified binaries;
  skipping hash verification.
- Installing the solver into the Blender extension directory.
- Modifying external installations or terminating external servers.
- Mixing add-on updates and solver updates into one operation.
- Releasing with failing tests; overwriting existing tags or artifacts.
- Inventing a custom Blender `index.json` schema.

## 19. Emergency Rollback

A defective published release is rolled back by publishing a new, higher version and
optionally marking the defective GitHub release as such in its notes. Channel
repositories are regenerated so the defective version is no longer the latest entry;
previously downloaded artifacts are never mutated. Tags are never deleted or moved.
If Pages content must be corrected, only the affected channel directory is
regenerated from verified artifacts.

## 20. Policy Change Procedure

## Public Dev snapshots

A Dev workflow may derive an authorized `X.Y.Z-dev.N` version only in its
isolated exact-commit checkout and must update package-internal metadata
consistently. It may modify only `gh-pages/dev/`; Stable, Beta, tags, GitHub
Releases, and the canonical source manifest are outside its authority.

Changes to this policy require an explicit, human-approved commit that modifies this
file, with the rationale in the commit message. Automation and AI assistants may
propose changes but must not weaken or bypass rules while executing a release. The
technical validators must be updated in the same change when a rule they enforce
changes.
