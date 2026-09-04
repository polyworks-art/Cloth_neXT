# Cloth NeXt Release Policy

> **Mandatory project policy**
>
> This document is the authoritative release and update policy for Cloth NeXt.
> Any developer, automation, AI coding assistant, or CI workflow modifying versions,
> tags, extension packages, update channels, solver compatibility metadata, release
> artifacts, or GitHub Pages publication must read and follow it.
>
> If another instruction conflicts with this policy, stop and request an explicit
> policy update. Do not silently bypass, reinterpret, or weaken these rules.

## 1. Purpose and authority

This policy governs how Cloth NeXt is versioned, packaged, validated, published to
Blender extension repositories, repaired, and updated. It is enforced by:

- `tools/validate_release_policy.py`
- `tools/scan_release_artifact.py`
- the test suite
- `.github/workflows/release-preflight.yml`
- `.github/workflows/release.yml`
- `.github/workflows/publish-dev.yml`
- `.github/workflows/repair-release-index.yml`

Cloth NeXt publishes only its own add-on code, declared Python dependencies,
release metadata, solver compatibility metadata, and the Cloth NeXt-owned Windows
Bake companion built from the selected source revision.

The external PPF Contact Solver is never bundled, mirrored, proxied, or republished.

## 2. Version source of truth

The canonical version lives exclusively in:

```text
cloth_next/blender_manifest.toml   ->   version = "STABLE.BETA.DEV"
```

For Stable and Beta publication, these values must agree:

```text
Manifest version
Git tag                       <version> or v<version>
Extension ZIP                 cloth_next-<version>-windows-x64.zip
release-manifest.json         cloth_next_version
solver_compatibility.json     cloth_next_version
Pages artifact directory      artifacts/<version>/
Blender repository index      version entry
```

No component may invent or independently derive a different version.

## 3. Version and channel rules

Cloth NeXt uses the three-position channel counter `STABLE.BETA.DEV`:

```text
STABLE.0.0       Stable, for example 2.0.0
STABLE.BETA.0    Beta, for example 2.2.0
STABLE.BETA.DEV  Dev, for example 2.2.5
```

Legacy `-beta.N`, `-rc.N`, and `-dev.N` values remain readable for compatibility but
are not produced by the current release workflows.

Rules:

- Versions are never reused or decreased.
- Automation never chooses a version on its own.
- Stable accepts only `STABLE.0.0`.
- Beta accepts only `STABLE.BETA.0` with `BETA >= 1`.
- Dev accepts only `STABLE.BETA.DEV` with `DEV >= 1`.
- A Stable release requires a previously successful Beta end-to-end test.
- New release infrastructure is exercised through Beta before Stable.
- Automation never promotes Beta to Stable on its own.

## 4. Release channels

Three Blender extension repositories exist:

- `stable/` exposes Stable only.
- `beta/` may expose Beta or Stable.
- `dev/` may expose Dev, Beta, or Stable.

Every repository index exposes exactly one active `cloth_next` candidate.

Publication is cumulative toward less stable channels:

```text
Stable -> stable, beta, dev
Beta   -> beta, dev
Dev    -> dev
```

Every target receives a byte-identical, SHA-256-verified archive.

Dev publication remains confirmation-gated, uses an exact source commit SHA, creates
no tag, and retains at most five immutable Dev archives.

## 5. Pages-only distribution

Cloth NeXt does **not** create GitHub Releases for Stable, Beta, or Dev.

The following are forbidden in Cloth NeXt release automation:

- `gh release create`
- `gh release upload`
- draft or prerelease GitHub Release objects
- GitHub Release assets used as the canonical package source
- repair workflows that depend on `gh release download`

Stable and Beta keep immutable Git tags as source anchors. Tags are not package
hosting and must never be moved, replaced, or force-pushed.

All package publication occurs through the `gh-pages` branch.

## 6. Canonical Pages artifact store

Each Stable or Beta version has one canonical immutable artifact directory:

```text
artifacts/<version>/
  cloth_next-<version>-windows-x64.zip
  release-manifest.json
  SHA256SUMS.txt
  RELEASE_NOTES.md
```

This directory is the authoritative repair source for that version.

Requirements:

- The ZIP is byte-identical to the tested release candidate.
- The ZIP is at most 100 MiB so GitHub can accept it as a `gh-pages` git blob.
- The manifest records the exact source tag, commit, channel, platform, companion,
  solver protocol requirements, ZIP name, and ZIP SHA-256.
- `SHA256SUMS.txt` matches the archive bytes.
- Release notes are present and non-empty.
- An existing `artifacts/<version>/` directory is immutable.
- Fixing a published artifact requires a new, higher version.

Channel repository copies are validated against the same tested ZIP. They are not an
independent source of truth.

## 7. Public exposure boundary

GitHub Pages is public hosting. Pages-only distribution reduces casual discovery and
removes the prominent GitHub Releases download surface, but it is **not** access
control, DRM, or a licensing system.

The Blender `index.json` necessarily exposes the active archive URL to configured
clients. Anyone who learns a public Pages URL may request it.

Therefore:

- public websites should not advertise or link directly to artifact directories;
- the add-on and Blender may use the fixed channel repository URLs;
- no code or documentation may claim that Pages prevents unauthorized downloading;
- actual buyer-only access would require an authenticated service, signed URLs, or a
  storefront-controlled download system outside this static repository design.

## 8. Required artifact contents

The Windows extension ZIP may contain exactly one Cloth NeXt-owned executable:

```text
bin/cloth-next-bake.exe
```

It must be validated by `companion_manifest.json`, including version, platform, size,
and SHA-256.

Beta and Stable archives must not contain `dev_build.json` or enable Developer Tools.
Dev-only UI is permitted only in explicitly prepared Dev snapshots.

Every Dev, Beta, and Stable archive must also contain:

```text
resources/onboarding/whats_new/<version>.json
resources/onboarding/assets/hero-panel.png
resources/onboarding/icons/<welcome-and-release-icon>.png
```

Welcome is invariant product-level content in the Companion and has no release JSON.
Its packaged hero and three fixed icons are nevertheless mandatory.
What's New is release-level content and must match the manifest version exactly.
Both are rendered by the single approved Companion executable; a second UI
executable is forbidden.

## 9. External PPF Solver policy

The PPF Contact Solver is external software developed and distributed by ST Tech / ZOZO.
It is not part of Cloth NeXt.

Cloth NeXt must never:

- commit the solver into this repository;
- include solver binaries, DLLs, runtime files, or archives in the extension ZIP;
- upload solver material to the canonical Pages artifact store;
- mirror the solver through Stable, Beta, or Dev repositories;
- host the solver under any Cloth NeXt download URL;
- present the solver as Cloth NeXt-owned software.

Cloth NeXt may download a manifest-pinned official upstream solver asset only after
explicit user confirmation and verification of source, size, SHA-256, protocol, and
schema compatibility.

## 10. Required release checks

A Dev, Beta, or Stable release must pass the applicable pipeline. Stable and Beta
pass, in order:

1. manifest, tag, and channel policy validation;
2. unit tests;
3. configured integration tests, with external prerequisites skipped honestly;
4. source structure validation;
5. solver compatibility manifest validation;
6. Welcome mode/asset and exact-version What's-New schema/content validation;
7. Companion build, mode smoke tests, clean-exit test, and integrity scan;
8. extension build through official Blender tooling;
9. built-artifact tests;
10. forbidden solver-material scan;
11. packaged ZIP validation, including onboarding resources and Companion modes;
12. canonical metadata generation;
13. canonical Pages artifact validation;
14. Blender repository generation through official Blender tooling;
15. cumulative channel separation validation;
16. atomic `gh-pages` publication.

A failing check publishes nothing.

## 11. Mandatory preflight

Before creating a Stable or Beta tag, the release manager must:

1. commit and push the intended release state;
2. run `release-preflight` against the exact commit;
3. inspect the generated `cloth-next-release-candidate` artifact;
4. verify source, Companion, Blender, package, scanner, and policy gates;
5. create the immutable version tag at that same commit only after success.

The tagged release workflow rejects a successful preflight from a different commit or
manifest version.

Preflight never tags, publishes, modifies Pages, or changes channel repositories.

## 12. Stable and Beta publication

A Stable or Beta tag triggers `.github/workflows/release.yml`.

The workflow:

1. rebuilds the release candidate through the reusable candidate workflow;
2. requires the matching successful preflight;
3. verifies the tag resolves to the exact checked-out commit;
4. generates and validates release metadata;
5. loads the current `gh-pages` state;
6. rejects an existing canonical directory for the version;
7. stages the canonical artifact set under `artifacts/<version>/`;
8. generates the required Stable/Beta/Dev repository copies and official indices;
9. validates the complete staged Pages state;
10. commits only the canonical version directory and allowed channel directories;
11. pushes atomically to `gh-pages`.

No GitHub Release is created before, during, or after this process.

## 13. Dev publication

Dev is published only through `.github/workflows/publish-dev.yml` with:

- exact 40-character source commit SHA;
- explicit Dev version;
- `PUBLISH_DEV` confirmation;
- isolated version metadata preparation;
- mandatory packaging, secret, Companion, Blender, and artifact checks.
- mandatory Welcome and exact Dev-version What's-New validation before build and
  again against the packaged archive;
- real Companion smoke tests for Bake, Welcome, What's New, invalid versions, and
  clean process exit.

Dev modifies only `gh-pages/dev/`, creates no tag, creates no GitHub Release, and does
not modify Stable or Beta.

## 13.1 Mandatory onboarding checklist for every channel

Before any Dev, Beta, or Stable publication, the release manager verifies:

- create and curate `resources/onboarding/whats_new/<version>.json` for the exact
  intended public version before starting the release build; automation must not
  generate or silently rewrite this editorial content from `CHANGELOG.md` or
  `RELEASE_NOTES.md`;
- Welcome mode and its fixed product assets are present, valid, offline-capable,
  and packaged; Welcome has no per-release JSON to curate;
- `whats_new/<version>.json` exists and its version equals the manifest, requested
  release version, package name, release notes, and changelog entry;
- titles, two to four highlights, optional improvement/fix lists, action kinds,
  package-relative asset paths, and HTTPS URLs are valid;
- the Companion manifest declares `bake`, `veyra`, `welcome`, `whats-new`, and
  `threadmark-worker`, and the built EXE accepts the matching CLI parameters;
- the hash-pinned ThreadMark Q models are provisioned at build time, embedded only
  in the owned Companion, and the worker's authenticated encode/shutdown smoke test
  passes without a runtime download;
- Fresh Install shows Welcome once without stacking What's New; Update shows What's
  New once; Same Version, re-enable, restart, and file-open do not repeat it;
- downgrade and channel-switch state remains monotonic and does not loop;
- manual Open Welcome / What's New / View Changelog actions work without mutating
  automatic seen state;
- register, file open, unregister, and Companion close leave no timer, Blender RNA
  reference, or process behind.

Any failure is a release-policy violation and publishes nothing. Because every
three-position Dev counter is an immutable public build in this repository, Dev
also requires exact-version What's-New content; the persisted seen-state prevents
the same build from producing repeat popups.

## 14. Blender extension repositories

Each channel `index.json` is generated exclusively by:

```text
blender --command extension server-generate
```

No custom index schema is invented.

Older immutable archives may remain beside the index, but the index exposes exactly one
active `cloth_next` candidate to avoid duplicate package-id ambiguity.

## 15. Add-on update behavior

Cloth NeXt never replaces its own loaded extension directory.

The add-on:

- reads the fixed Pages channel `index.json` only to report update status;
- synchronizes the selected repository when explicitly requested;
- opens Blender's native extension update view;
- leaves installation to Blender's extension manager.

Update actions are blocked while solver startup, transfer, simulation, frame fetching,
cache writes, cancellation, or owned process shutdown are active.

Release-note links must point to the Cloth NeXt website, not GitHub Releases.

## 16. Immutability

Published tags, canonical artifact directories, channel archives, manifests, and
checksums are immutable.

The workflow aborts when:

- the version directory already exists;
- a same-named channel archive has different bytes;
- metadata disagrees with the archive, tag, commit, or channel;
- a channel index exposes an ineligible or ambiguous candidate.

A bad published version is superseded by a higher version. It is never rewritten.

## 17. Failed publication handling

If publication fails before the Pages push:

- `gh-pages` remains unchanged;
- existing channel indices remain valid;
- no partial canonical artifact becomes public;
- no GitHub Release cleanup is necessary because no GitHub Release exists.

Diagnose, fix, rerun preflight, and publish a new version when the failed version was
already made public. The same version may be retried only when no canonical directory
or channel archive for it was ever published.

## 18. Repair workflow

`.github/workflows/repair-release-index.yml` repairs one selected channel from the
canonical Pages artifact store.

It must:

- require explicit channel, version, and confirmation;
- verify the requested release is eligible for the selected channel;
- verify ZIP, manifest, checksums, release notes, tag, and commit;
- preserve existing immutable archives;
- generate an official single-candidate index with real Blender;
- modify only the selected channel index and a missing byte-identical archive;
- never download from or depend on GitHub Releases.

The canonical artifact directory is never modified by repair.

## 19. AI-assisted release procedure

When instructed to release Cloth NeXt, an AI assistant must:

1. read this policy;
2. verify repository, branch, and working state;
3. use the exact human-supplied version;
4. determine the channel;
5. verify the version and canonical Pages directory do not already exist;
6. update manifest, compatibility metadata, changelog, and release notes;
7. run all required checks;
8. build and inspect the extension locally where possible;
9. commit and push the release preparation branch;
10. run and verify preflight for the exact commit;
11. create the immutable Stable/Beta tag only after approval;
12. let `release.yml` publish exclusively through Pages;
13. verify the canonical artifact and every affected channel repository;
14. report success only after publication is complete.

It must never choose a version, skip tests, overwrite artifacts, create a GitHub Release,
or include PPF solver material.

## 20. Emergency rollback and policy changes

A defective release is rolled back by publishing a new higher version and regenerating
the affected channel indices so the defective version is no longer active. Previously
published artifacts and tags remain immutable.

Changes to this policy require explicit human approval and a reviewed commit that
updates technical validators and workflows in the same change. The Pages-only policy
was explicitly approved to reduce casual package discovery while preserving Blender
repository updates and release traceability.
