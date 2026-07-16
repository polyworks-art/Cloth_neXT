# Changelog

## 0.4.0 — 2026-07-16 (Beta channel)

### Added

- Multi-object Cloth, Rod, and Soft Body bakes with Follow Animation pins.
- All PPF-supported Empty forces with keyframe animation.
- Optional Colliders; colliderless projects use a remote internal PPF STATIC
  sentinel without adding an object to the Blender scene.
- Configurable Bake RAM auto-cancel, enabled at 90% by default and debounced
  across two telemetry samples.

### Changed

- Resource HUD now focuses on CPU, RAM, and VRAM with clearer live graphs and
  a red RAM safety threshold.
- Bake companion uses smoothly drifting, rotating Cloth NeXt icons.
- Release versions use the `STABLE.BETA.DEV` channel-counter scheme.

### Fixed

- Soft Body and Rod bake paths, multi-object material/pin encoding, Empty
  registration, dropdown icons, and numeric Dev-channel update detection.

## 0.3.0-dev.13 — 2026-07-14 (Dev test channel)

### Added — animated and deforming Colliders

- Add Static/Animated Collider Motion controls with Static remaining the
  backward-compatible default.
- Capture evaluated rigid transforms and stable-topology mesh deformation from
  Blender across the Bake range, including parent, constraint, driver,
  Shape-Key, Armature, and modifier evaluation.
- Encode the verified PPF 0.11 `transform_animation` and
  `static_deform_animation` contracts, including deterministic support for
  multiple mixed-motion Colliders.
- Validate Collider topology per frame and restore Blender's original frame
  after successful capture, cancellation, or failure.
- Keep Collider animation under Blender control: only Cloth receives a PC2
  playback cache and Collider output is never written back.

### Fixed

- Do not report a successful Cloth playback attachment as an import failure
  when only post-import metadata or stale-cache housekeeping fails.

## 0.3.0-dev.2 — 2026-07-13 (Dev test channel)

- Prevent older, equal, invalid, or ambiguous channel candidates from enabling
  the update handoff; revalidate the channel index after repository sync.
- Add object-local Cloth Pressure through properties, immutable model, SHELL
  `pressure` encoding, diagnostics, metadata, and cache fingerprints.
- Add scene-wide Time Step, Newton, and PCG Quality with verified PPF mappings.
  `dt = 0.001` preserves existing Cloth NeXt behavior; no `substeps` key exists.

All notable Cloth NeXt changes. Versioning follows
[docs/RELEASE_POLICY.md](docs/RELEASE_POLICY.md); the canonical version lives in
`cloth_next/blender_manifest.toml`.

## Unreleased

### Added — Phase 4 production cache metadata and integrity

- Publish a versioned sidecar for every new PC2 with explicit partial/complete
  states, deterministic scene/object/settings/geometry fingerprints, runtime
  identities, exact layout, and material/quality/range details.
- Authenticate every PC2 byte and every semantic metadata field independently
  with SHA-256 before attaching playback.
- Detect missing, partial, corrupt, settings-stale, and geometry-stale caches;
  failed or cancelled Bakes can never be presented as complete.
- Invalidate for deformable/Collider position or topology changes, transforms,
  ordinary Action keyframes, Pinning, FPS/range, materials, and quality while
  keeping the panel draw path mesh-free.
- Extend Clear Result to remove owned Rod playback and sidecars without
  traversing or modifying unrelated files.

### Added — experimental Rod / Cable and Soft Body workflow

- Add Rod / Cable and Soft Body Physics roles alongside Cloth and Collider.
- Encode PPF `ROD` edge geometry and volumetric `SOLID` surface input with
  validated ARAP material parameters and selectable PPF tetrahedralization.
- Preserve Bezier and Poly Curve objects during Rod playback by keyframing
  control points and handles from the verified result stream.
- Map tetrahedral Soft Body output back to the original surface through the
  solver-provided surface map, while rejecting open/non-manifold source meshes.
- Add pure-Python contract tests, a real-solver Rod/Solid harness, and a Blender
  workflow smoke test. Pinning for these two roles remains intentionally hidden.

### Changed — bounded-memory PC2 playback generation

- Stream complete PPF frames directly through NumPy extraction and vectorized
  coordinate conversion into a transactional PC2 writer.
- Publish PC2 and metadata atomically, retain the previous valid cache on a
  failed Rebake, and expose per-frame cache creation/finalization progress.
- Replace TCP chunk accumulation with one bounded preallocated receive buffer
  and split transfer, decode, extraction, transform, write, and finalization
  diagnostics.
- Add reproducible 10k/50 and 50k/250 synthetic benchmarks. The measured
  medium baseline fell from 77.853 s and 2.115 GB peak Python allocation to
  0.385 s and 4.28 MB on the same workstation. Solver parameters, including
  the `dt = 0.001` default, are unchanged.

### Fixed — solver update detection uses the immutable release identity

- A managed PPF Contact Solver installation is now identified by the immutable
  official release tag plus the asset SHA-256 (`current.json` metadata
  version 2), no longer by the internal solver package version alone: a new
  official release that still reports package `0.1.0` is now correctly
  detected as an available update.
- New managed installations live under `versions/<official-release-tag>/`, so
  official releases sharing one internal package version install side by side.
  The previously active installation stays untouched and active until the new
  release passed SHA-256, version-probe, protocol, schema, and the real health
  check.
- Legacy `current.json` files (only `active_version`) stay readable and
  startable, are never rewritten just by reading them, and are offered the
  manifest-pinned release as a compatible update because their exact official
  release identity is unknown.
- The same release tag appearing with a different manifest hash is logged and
  handled as an integrity/manifest problem, never as a silent release switch.

### Added — solver update notice in the add-on preferences

- The preferences show a red alert box ("Solver Update Available") immediately
  when a managed installation is older than the manifest-pinned verified
  release. The comparison is purely local against the bundled
  `solver_compatibility.json` — no network request, no thread, no download.
- The alert's "Install Compatible Solver Update" button opens the existing
  confirmation-gated installer dialog; downloads never start automatically.
- External installations are never modified and never falsely reported as
  outdated.

## 0.3.0-beta.1 — 2026-07-13 (beta channel)

### Changed — gated developer interface

- Grouped Real Solver Test and UI Diagnostics into one preference-gated
  Developer Tools subpanel under Cache, using one native alert-styled area.
- Developer controls are hidden by default and are enabled only in explicitly
  prepared Dev snapshots; Beta and Stable release validation rejects Dev build
  metadata.

### Added — static vertex-group Pinning

- Added static hard Pinning through Blender vertex groups.
- Added binary Pin membership to cache fingerprints.
- Added source/evaluated topology-safety validation for Pin indices.

### Fixed — companion-gated modal startup

- Fixed production Bake locking Blender before the Bake companion window
  became visible.
- Rebake and Bake Again now replace only the active object's validated Cloth
  NeXt cache after all startup prerequisites succeed.

### Added — arbitrary ranges and modal Bake companion

- Added arbitrary Bake ranges and corrected non-frame-one PC2 mapping.
- Added a responsive modal Blender workflow and foreground Bake window.
- Added automatic companion shutdown and companion crash recovery.

### Added — production-facing Bake workflow

- The Physics Properties Solver panel now provides the main Bake/Rebake/Bake
  Again action, solver readiness, supported scene summary, typed progress,
  cancellation, and cache state using the existing custom icon family.
- Bake uses the same validated immutable Phase-3B material `RunPlan` as the
  developer real-solver diagnostics. The optional Bake companion launches or
  reuses according to Add-on Preferences; launch failure is a warning and the
  simulation continues through Blender's HUD and Physics UI.

### Fixed — Dev repository duplicate package metadata

- Dev repository generation now exposes only the newest `cloth_next`
  candidate in the official Blender index while retaining older immutable ZIPs.
  This prevents duplicate package IDs from making Blender display Dev 1 after
  downloading Dev 5 or continuously offering the same update. Existing
  Blender repository caches may need an explicit refresh or removal.

### Fixed — critical updater self-install crash

- Blender could crash when clicking the add-on update install button:
  Cloth NeXt invoked `bpy.ops.extensions.package_install` for its own
  package from its own running operator, letting Blender disable, replace,
  and reload the extension while its code was still executing on the
  Python stack — a native module-lifetime hazard no try/except can catch.
  The self-install call path was removed entirely. The button is now
  *Update through Blender*: it stops only Cloth NeXt-owned solver
  processes, closes the owned Bake companion, synchronizes the exact
  selected Stable/Beta/Dev repository, and opens Blender's native
  extension update view where the user completes the installation.
  The misleading `INSTALLING`/`RESTART_REQUIRED` session states were
  replaced by `READY_IN_BLENDER` (opening the update view proves no
  installation). A structural policy test
  (`tests/test_update_selfinstall_policy.py`) fails the suite if any
  production code calls `package_install`, replaces the active extension
  directory, extracts an update ZIP, or schedules a timer-deferred
  self-install.

### Phase 3B — real material parameters

- Phase 3B.1 aligns the immutable snapshot and Blender property identifiers
  with the artist-facing contract (`surface_weight`, `shape_damping`,
  `fold_damping`, `collision_gap`, and `surface_offset`) while preserving
  their exact PPF wire mappings and calibrated values.
- Real Shell/Static material mapping: Material, Damping, and Collision
  properties are captured immutably on the main thread, validated, and
  encoded into the exact PPF `young-mod`/`poiss-rat`/`bend`/`friction`/
  `contact-gap`/`contact-offset`/`strain-limit` wire keys (float32-exact,
  matching the official encoder). Enable Contact maps to
  `scene.disable-contact`.
- Bundled read-only PPF fabric presets (Silk, Flag, Cotton, Wool, Denim,
  Leather, plus Default Cloth and Custom) with pinned upstream provenance
  and preserved Apache-2.0 notice; selecting a preset applies its values,
  manual edits switch to Custom without resetting anything.
- Artist-facing terminology (Surface Weight, Stretch Resistance, Sideways
  Response, Bend Resistance, Surface Grip, Stretch Limit, Maximum Stretch,
  Shape Damping, Fold Damping, Collision Gap, Surface Offset) with the
  technical PPF parameter named in every tooltip and in Advanced PPF.
- Removed/hid the misleading placeholder controls: Quality
  (substeps/iterations), Total Mass, Thickness, Stretch/Shear stiffness,
  per-mode damping, Velocity damping, Self Collision, Pressure, Shape/pin
  settings, and the editable Cache range (replaced by the read-only
  "Development slice: Blender frames 1–8" notice). Old placeholder values
  were never used by the solver and are not reinterpreted.
- Corrected the Shell density unit and default: Surface Weight is an area
  density in kg/m² with default 1.0 (previously mislabeled 1000 kg/m³);
  Stretch Resistance is the direct density-normalized PPF young-mod value
  (never divided by density; double-normalization is regression-tested).
- Encoded-parameter inspector (Developer Tools): shows artist and wire
  names with exact values and copies JSON diagnostics — without starting
  the solver.
- Minimal versioned cache-invalidation metadata: a material fingerprint on
  the baked object plus a `*.meta.json` sidecar marks results stale when
  any mapped setting changes; nothing is deleted automatically.

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
# Unreleased

- Fixed false Pinning topology errors caused by Cloth NeXt-owned Mesh Cache playback.
- Added animated vertex-group Pin targets and cancellable per-frame target capture.
- Reworked the companion mist into a full rectangular amber-and-anthracite fog field with seamless build-generated animation frames.
- Replaced the static Bake companion icon with a lightweight animated mist visualization and added live solver activity to the lower status bar.
- Matched the packaged Windows companion title bar to the main window's dark gray background.
