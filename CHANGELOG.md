# Changelog

All notable Cloth NeXt changes. Versioning follows
[docs/RELEASE_POLICY.md](docs/RELEASE_POLICY.md); the canonical version lives in
`cloth_next/blender_manifest.toml`.

## Unreleased

### Dev channel

- Added the explicit public `0.2.0-dev.N` snapshot channel with permanent risk
  warnings, exact repository targeting, immutable packages, five-build
  retention, and a reduced but mandatory safety-validation workflow.

### Added

- Cached hardware telemetry for NVIDIA GPU/VRAM and Windows CPU/RAM metrics,
  with safe stale/unavailable states and no draw-thread polling.
- Responsive compact/expanded Bake HUD and custom Physics panel header icons.
- Automatic, reusable Bake companion launch for explicit real solver tests.

### Fixed

- Real solver runs are typed and no longer labeled as UI previews.
- Solver-output frames 1--7 display as Blender frames 2--8, and Finished
  explicitly reports frame/progress 8 of 8.

## 0.2.0-beta.6 — 2026-07-12 (beta channel)

### Added

- First real Phase 3A PPF solver vertical slice.
- Blender scene snapshot for one cloth and one static collider.
- Exact PPF 0.11 Scene and Param encoding with float32-correct CBOR scene payloads.
- Typed upload, build, simulation, cancellation, and result-transfer protocol.
- Incremental retrieval and validation of eight complete playback frames.
- Constant-topology Blender PC2 playback cache.
- Real shared bake status through Physics panels, HUD, and companion.
- Developer operators for creating and running the PPF test scene.
- Opt-in real pinned-solver integration coverage.
- Worker-thread `bpy` access protection and lifecycle cleanup tests.

### Fixed
- Automatic add-on update failed in real Blender 5.1.2 with "Repository not
  set": the extension operators' `repo_index` parameter counts only enabled
  repositories with valid settings, so an index into
  `preferences.extensions.repos` silently shifts when any earlier repository
  is disabled. The update now identifies the channel repository by its
  resolved `directory` RNA and uses Blender's own per-package update operator
  (`extensions.package_install(repo_directory=…, pkg_id=…)`) instead of
  `package_upgrade_all` + `active_repo`, so only Cloth NeXt is ever updated.
- Distinct update error states: repository disabled and repository
  synchronization failed are now reported separately, and the fallback
  message says the repository was synchronized before pointing to Blender's
  update view.

### Added
- Real Blender runtime smoke test for the update path
  (`tools/blender_update_smoke_test.py`, wired into CI), covering the
  disabled-repository condition that previously raised "Repository not set",
  exact-repository synchronization, unrelated repositories staying untouched,
  and the manual fallback.
- Incomplete-frame handling and schema/wire-format mismatches.
- Companion cancellation propagation and worker, timer, and subscription cleanup.

### Experimental

Phase 3A currently supports one cloth, one static collider, a small verified
material subset, eight test frames, constant topology, and developer-oriented
test execution. Interactive Blender validation is still required.

### Important

This release does not yet provide general production baking, multiple cloths or
colliders, animated colliders, pins, pressure, sewing, tearing, production cache
metadata, complete material UI mapping, live solver preview, or remote solver
hosts. The PPF Contact Solver remains separately installed external software and
is not included in the Cloth NeXt package.

## 0.2.0-beta.5 — 2026-07-12 (beta channel)

### Fixed
- The bundled Bake companion in 0.2.0-beta.4 was built from an older UI
  state. beta.5 ships the intended compact 370x108 window with a responsive
  progress bar and label and a pack-based bottom row instead of fixed pixel
  positions.

## 0.2.0-beta.4 — 2026-07-12 (beta channel)

Publishes the Phase 2.8B UI preview through the corrected, preflight-verified
release pipeline.

### Added
- Mandatory unpublished release preflight (`release-preflight.yml`) building
  and validating the exact candidate commit before any tag is created.
- Exact commit SHA and manifest version verification
  (`tools/check_release_preflight.py`) gating publication.

### Fixed
- Build-time Pillow dependency installation order.
- Companion build and staging order in the release candidate workflow.
- Clean separation between source tests and built-artifact tests; Windows-only
  EXE assertions now run only after the Windows build.

### Important
- The Bake workflow remains a UI preview: no PPF scene export, real cloth
  simulation, frame transfer, result import, or real cache generation yet.
- The external PPF Contact Solver remains separate and is not bundled.

## 0.2.0-beta.3 — 2026-07-12 (beta channel)

### Added
- Compact dark Cloth NeXt Bake companion aligned with a native DCC progress
  dialog, using the Cloth NeXt application identity and croissant progress icon.
- CI-built Windows companion bundled at `bin/cloth-next-bake.exe`, guarded by a
  strict version/platform/size/SHA-256 manifest before launch.
- Coordinated Ubuntu validation, Windows extension build, and gated publishing
  jobs with one shared build-dependency declaration.

### Fixed
- CI now installs Pillow and the deterministic icon tooling before test
  collection, correcting the failed immutable beta.2 release attempt.
- Companion shutdown is performed before add-on replacement without mixing its
  ownership with PPF solver ownership.

## 0.2.0-beta.2 — 2026-07-12 (beta channel)

### Added
- Phase 2.8B role-aware Physics Properties panels for Cloth and Collider.
- Shared immutable bake status controller used by panels, the display-only
  Viewport HUD, preview workflow, and optional companion source application.
- Deterministic monochrome runtime icon system, including the croissant Bake
  icon, with mandatory build-time generation and validation.
- Authenticated, bounded localhost IPC and exact-child companion ownership with
  explicit launch and reload-safe cleanup.

### Important
- Bake controls remain an unmistakable UI preview. This version does not export
  PPF scenes, simulate cloth, transfer frames, import results, or generate real
  caches. The real PPF pipeline remains Phase 3.
- This beta requests interactive visual QA for panels, icons, HUD layout,
  viewport resizing, Blender scaling, Windows DPI, and companion behavior.
- The locally buildable companion EXE is not distributed.

## 0.2.0-beta.1 — 2026-07-12 (beta channel)

### Added
- **Add-on update workflow** in the Add-on Preferences: installed version
  display, Stable/Beta update channel selection (defaults to Beta while a
  prerelease is installed), an explicit *Check for Updates* action against the
  official channel repositories, *Install Update* through Blender's own
  extension mechanism (`extensions.repo_sync` + `extensions.package_upgrade_all`),
  an *Add Channel Repository* action (explicit, duplicate-safe), *Open Blender
  Extensions* fallback, and *Open Release Notes*. Cloth NeXt never replaces its
  own extension files and never mixes add-on updates with PPF solver updates.
- Strict pure-Python version parser for the policy-supported forms
  (`X.Y.Z`, `X.Y.Z-beta.N`, `X.Y.Z-rc.N`) with correct beta/rc/stable ordering.
- Update install path enforces `addon_update_guard`: blocked in every
  application state that is not explicitly update-safe; stops only solver
  processes Cloth NeXt started itself and never touches external servers or
  the separately installed solver.
- **Phase 2.8A Physics Properties integration**: a native "Cloth NeXt" entry
  below Blender's Add Physics buttons, per-object enable/remove operators, a
  Cloth NeXt panel in Physics Properties with the object role (Cloth/Collider),
  and persistent per-object settings. No N-panel; simulation controls follow in
  the next phase.

### Fixed
- The *Download Official Solver* button did nothing: an operator subclass of a
  registered operator corrupted Blender's RNA↔Python mapping, silently skipping
  the confirmation dialog. The shared dialog behavior now lives in a plain
  mixin.
- Solver download now shows real progress in the preferences (with a
  reload-safe UI refresh timer), respects Blender's *Allow Online Access*
  setting, and surfaces installer errors visibly instead of failing silently.
- Blender smoke test runs reliably: source-tree fallback on Linux CI (the
  extension is Windows-only), `is_registered`-based assertions, and CI creates
  the build output directory.

### Notes
- The PPF Contact Solver remains external ST Tech / ZOZO software, installed
  separately after explicit confirmation; it is not part of this package.
- Add-on updates install exclusively through Blender's extension system from
  the policy-defined channel repositories; restart Blender after an update.

## 0.1.0 — 2026-07-12

- Initial release: pure core (state machine, errors, events, logging), PPF
  health check and process manager, verified solver bootstrap, managed solver
  installer with confirmation/SHA-256/health gates, release pipeline with
  Stable/Beta Blender extension repositories, Phase 2.7 hardening.
