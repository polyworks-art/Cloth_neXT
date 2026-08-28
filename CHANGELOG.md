# Changelog

## 2.3.4 - 2026-08-28

### Fixed

- Owned PPF server output is redirected to Cloth NeXt-owned real log files
  instead of Windows anonymous pipes, preventing solver logging from blocking
  Tokio control-server workers while the process remains alive.
- Transient status timeouts, resets, and connection refusals during build or
  simulation now enter a bounded reconnect state instead of immediately
  surfacing `CNX-E140`. Lifecycle commands remain exactly-once.
- Persistent transport loss retains `CNX-E140` with precise connect/read/reset
  classification, bounded latency counters, process-tree evidence, and solver
  log tails.
- Absolute PC2 playback now mutes Armature and Corrective Smooth inputs whose
  evaluated deformation is already baked into the cache, preventing a 90° body
  rotation from becoming an apparent 180° skirt rotation and position offset.
- Re-Bake, animated Pin capture, rollback, and Clear Cache temporarily restore
  or permanently recover the artist's original modifier visibility states.

### Validation

- Regression coverage verifies transient and persistent transport failures,
  cancellation while reconnecting, exactly-once simulation start, malformed
  responses, heavy solver output, invalid UTF-8, and log lifecycle cleanup.
- The normal repository suite passes with 1,649 tests; 10 configured external
  integration cases skip honestly and 3 built-artifact cases are deselected.
- Real official PPF 0.18/schema 2 health, ownership, single-object, and
  multi-object integration tests pass. A measured run completed 75 status
  requests with no failures and 141 ms maximum observed latency.

## 2.3.2 - 2026-08-24

### Fixed

- Rebake authenticates stale recovery partials against their durable project
  metadata when the current run has a newer recovery identity, avoiding a false
  `CNX-E100` cache-ownership failure.
- Clear Cache scans all scene objects when invoked from a Collider and removes
  authenticated recovery partials belonging to Cloth objects without weakening
  protection for foreign or unauthenticated files.

### Validation

- Regression coverage verifies both cross-generation recovery ownership and
  scene-wide cleanup from a non-Cloth active object.
- The normal non-integration repository suite passes with 1,640 tests.

## 2.3.1 - 2026-08-24

### Added

- A single ownership-authenticated safe-delete service now handles Cloth NeXt
  cache, PC2, metadata, recovery, export, updater, solver-work, and temporary
  artifacts with bounded Windows retry/backoff and same-root tombstones.
- Cache scans and safe lifecycle points remove authenticated
  `.clothnext-delete-*` tombstones left by short-lived file locks.
- The public error catalogue now has dedicated codes for animated Collider
  capture (`CNX-E128`), invalid recovery checkpoints (`CNX-E129`), and occupied
  local solver ports (`CNX-E136`).
- The canonical error registry now generates both the public Markdown catalogue
  and GitHub Pages `errors/errors.json` feed during Dev and tagged publication.

### Fixed

- Playback readers, cache modifiers, PC2 writers, worker threads, the Bake
  Companion, and owned solver processes are released before their files are
  removed, preventing recurring `CNX-E193` cleanup failures on Windows.
- Sharing violations, antivirus scans, and delayed handle release no longer
  invalidate an otherwise completed Bake when an obsolete owned artifact can
  be safely tombstoned for later cleanup.
- Cache cleanup retains strict owned-root authentication, protects legacy or
  unowned caches, rejects out-of-root paths, and never terminates foreign
  processes or deletes unknown user files.
- Solver readiness, native-worker quarantine, stage-specific process exits,
  recovery mismatch, animated Collider topology, result-transfer, playback,
  Rod Curve, multi-object cache, cleanup permission, intersection, convergence,
  instability, and memory failures now resolve to their accurate public codes.
- Structured `failure_kind`, `crash_kind`, and active-operation diagnostics now
  take precedence over fragile generic error text without hiding unknown
  failures behind broad exception handling.

### Validation

- The focused cleanup lifecycle suite covers immediate/missing deletion,
  transient Windows sharing failures, bounded exhaustion, tombstoning and later
  removal, unsafe and legacy paths, completed-cache preservation, cancellation,
  finalization, metadata integrity, and idempotency.
- The error audit adds positive coverage for every specific classifier,
  historical negative cases, structured crash diagnostics, stable-code
  compatibility, and exact runtime/Markdown/Pages-feed synchronization.
- The normal non-integration repository suite passes with 1,637 tests; source
  compilation and extension validation also pass.
- The external PPF Contact Solver remains separate, unmodified, and unbundled.

## 2.3.0 - 2026-08-22

### Added

- VEYRA provides an artist-facing Analyze, Repair, and Validate workflow for
  solver-confirmed self-intersections without starting a cloth simulation.
- Safe topology cleanup can repair exactly diagnosed duplicate-position
  vertices through bounded local welds while preserving ambiguous seams,
  layers, attributes, materials, Shape Keys, linked data, and shared meshes.
- Deterministic region repair uses topology-aware sheet assignment, adaptive
  movement candidates, local geometry checks, strict global validation, and
  exact rollback for every rejected change.
- A generalization corpus and frozen adversarial holdouts cover layered,
  folded, multi-sheet, multi-object, transformed, reordered, and intentionally
  coincident cloth instead of relying on one production garment.

### Fixed

- VEYRA keeps one Companion process and job active through analysis, repair,
  revalidation, and contact validation, avoiding intermediate error flashes or
  visible restarts between successful passes.
- Geometry diagnostics collect repairable issues across all Cloth objects and
  retain object-bound vertex identity after topology changes.
- Solver contact totals, overlays, details, and final states now come from the
  same fresh validation result and stale results are discarded.
- Cancelled and failed repair candidates restore exact coordinates and
  topology, release cached session data, and leave no partially applied change.
- Companion startup, readiness, cancellation, terminal shutdown, stale-job
  isolation, and Blender shutdown cleanup were hardened.

### Validation

- Blender 5.2.0 LTS passed repeated registration, file lifecycle, Unicode path,
  scene switching, owned-cache cleanup, and packaged Companion lifecycle tests.
- Real Velune (protocol 0.13) and Lumen (protocol 0.18) multi-object runs each
  simulated two Cloth objects with a collider and imported all requested frames.
- The real VEYRA/Lumen regression repaired all 18 diagnosed degenerates and
  reduced contacts monotonically from `2077 -> 2072 -> 1667 -> 1571 -> 1566`.
  Three region transactions were accepted, none rejected, and five Lumen BUILD
  validations ran without starting frame simulation.
- The same VEYRA Companion process was reused for the complete run, and the
  original Blend remained byte-identical with SHA-256
  `8402CE65A13A4D375985FDB681745F7FEBB93AFBBA6665B68831AF38F0D122B3`.
- The external PPF Contact Solver remains separate, unmodified, and unbundled.

## 2.2.48 - 2026-08-22

### Added

- A reproducible VEYRA generalization corpus covers clean cloth, intended
  duplicate seams, stacked layers, near duplicates, folded and multi-sheet
  contacts, independent regions, scale changes, semantic discontinuities,
  multiple objects, and adversarial false-positive cases.
- Frozen holdout cases verify folded cuffs, decorative patches, and three-layer
  contact chains without feeding their results back into heuristic tuning.
- The VEYRA heuristic audit documents every important topology, region,
  ranking, displacement, batching, cache, and authoritative-validation rule.

### Fixed

- Safe topology repair now considers only vertices from solver-diagnosed
  intersections instead of scanning unrelated coincident geometry as eligible
  repair targets.
- Disconnected islands require a coherent coincident boundary chain and
  opposite surface continuation before an explicit-ID weld is allowed. Object
  membership alone no longer treats lining, pockets, or stacked panels as
  import seams.
- Multi-sheet contact graphs are no longer forced into two sides merely because
  their constraint graph is bipartite; unresolved regions remain fail-closed.
- Adaptive displacement evaluates every locally safe 1%, 2%, 4%, and 8%
  candidate and ranks measured crossing reduction, geometry margin, and
  movement cost instead of assuming the strongest candidate is best.
- Weld eligibility remains protected by object identity, point attributes,
  vertex groups, face/corner semantics, material assignments, seam/sharp flags,
  Shape Keys, linked/shared data, and strict authoritative rollback.

### Validation

- Full Python suite: 1,543 passed, 9 skipped, 3 deselected.
- The clean/generalization corpus produced zero destructive false-positive
  repairs; all three previously frozen holdouts passed on their first run.
- Blender 5.2.0 LTS and the real Lumen solver reduced the updated production
  scene monotonically from `2077 -> 2072 -> 1682 -> 1469 -> 1132` while
  repairing all 18 Top degenerates and accepting three region iterations.
- Only three structurally proven Shorts weld clusters were accepted; 145
  unproven coincident boundary clusters were protected rather than guessed.
- No frame simulation started, one Companion process was reused, and the
  original Blend remained byte-identical with SHA-256
  `8402CE65A13A4D375985FDB681745F7FEBB93AFBBA6665B68831AF38F0D122B3`.
- The external PPF Contact Solver remains unbundled and unmodified.

## 2.2.47 - 2026-08-22

### Added

- Veyra provides a dedicated Cloth NeXt Companion workflow for repairing
  solver-confirmed self-intersections without starting frame simulation.
- Deterministic intersection regions retain object-bound vertex identity,
  bounded two-ring patches, adaptive 1%, 2%, 4%, and 8% displacement
  candidates, local geometry safety checks, and exact transactional rollback.
- Independent regions can be combined into one authoritative transaction when
  their vertices and expanded triangle patches are provably disjoint; failed
  batches are subdivided deterministically.

### Fixed

- Solver contact totals, detailed pairs, mapped pairs, overlays, and Bake
  window state now retain one consistent authoritative diagnostic result.
- Veyra keeps one Companion job and PID through Analyze, Solve, Apply,
  Revalidate, and Validate Contacts instead of flashing an intermediate error
  or restarting between accepted passes.
- Follow-up validation reuses immutable topology, adjacency, object metadata,
  Params, and export structure while refreshing only current vertex positions.
- Candidate ranking favors dense, high-value safe regions and local crossing
  validation no longer recomputes triangle points and bounds for every pair.
- Session caches are released after success, cancellation, and failure.

### Validation

- Full Python suite: 1,501 passed, 9 skipped, 3 deselected.
- Three real Blender 5.2.0 LTS/Lumen runs produced the identical monotonic
  chain `2129 -> 1777 -> 1584 -> 1224`, repairing 905 intersections (42.51%).
- Authoritative BUILD calls fell from 7 to 4. Measured total Veyra times were
  79.61 s, 92.31 s, and 106.30 s versus the 162.14 s baseline.
- All 18 Top degenerates were repaired through the existing exact local weld
  path. Shorts used position-only repairs, no frame simulation started, and
  the original Blend remained byte-identical.
- The external PPF Contact Solver remains unbundled and unmodified.

## 2.2.46 - 2026-08-20

### Fixed

- Geometry preflight now collects degenerate faces and locally detectable
  intersections across every deformable before blocking Bake, instead of
  stopping at the first object or issue type.
- Degenerate triangles are retained for repair diagnostics but excluded from
  intersection candidates, preventing artificial double-counting.
- Local intersection diagnosis reuses the authoritative strict-crossing and
  coplanar-overlap predicates after a Blender BVH broad phase; normal mesh
  adjacency and shared source vertices remain excluded.
- Auto Fix now discards topology-stale diagnostics and immediately rebuilds a
  fresh local snapshot and overlay without starting Lumen.
- Exact local welds remain the repair for safely diagnosed duplicate-position
  degenerates, while supported intersections continue to use bounded nudging.

### Validation

- Full Python suite: 1,447 passed, 9 skipped, 3 deselected.
- Targeted geometry, UI, snapshot, Auto Fix, multi-object, mapping, adjacency,
  and performance coverage: 186 passed.
- Blender 5.2.0 LTS validated the updated real `IntersectionTest.blend`: the
  first pass found all 18 current Top degenerates and blocked solver startup.
- The strict/coplanar local pass reduced 62,462 triangles through BVH to 14,167
  candidates and 122 narrow-phase tests, with no confirmed intersections in
  the updated scene.
- Auto Fix repaired all 18 degenerates through 18 explicit local welds; the
  fresh post-fix snapshot reported zero degenerates and zero intersections.
- The source Blend remained byte-identical with SHA-256
  `1C1AED91BDB5A5C3A63B0A49710CAD7B900E01665AD7F6913B8A553A384D603F`.
- The external PPF Contact Solver remains unbundled and unmodified.

## 2.2.45 - 2026-08-20

### Fixed

- Degenerate-face repair now derives its minimum correction from local face
  geometry instead of Cloth Collision Gap or Surface Offset.
- Distinct diagnosed vertex IDs at effectively identical positions use only
  explicit, prevalidated BMesh weld target maps; Auto Fix never performs a
  radius-based Merge by Distance.
- Local weld candidates fail closed for linked or shared meshes, Shape Keys,
  incompatible vertex groups or point attributes, collateral face removal,
  material changes, new degenerates, duplicate faces, or additional
  non-manifold geometry.
- Position-based degenerate corrections and bounded intersection corrections
  are checked against relevant local triangles before they are accepted.
- Independent safe repair clusters and degenerate fixes remain applicable when
  another reported intersection cannot be repaired safely.
- Intersection validation now reuses the authoritative diagnostic strict- and
  coplanar-overlap helpers instead of maintaining divergent geometry tests.

### Validation

- Full Python suite: 1,440 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS built and validated the Windows Dev extension locally; all
  3 packaged-artifact tests, the Companion scan, and the forbidden-solver
  material scan passed.
- Blender 5.2.0 LTS and the real Lumen solver repaired all 40 zero-area faces
  in `IntersectionTest.blend` through 15 explicit weld groups without moving
  any surviving vertex positions.
- The approved local topology delta was 33 vertices, 75 edges, and 40
  diagnosed polygons; duplicate faces stayed at zero and non-manifold edges
  decreased by 30.
- The 36,666 Top intersection pairs already present in the source scene did
  not increase. The isolated Shorts pair 15970 / 18393 remained one confirmed
  intersection and was skipped without changing geometry after no safe
  bounded candidate was found.
- The external PPF Contact Solver remains unbundled and unmodified.

## 2.2.44 - 2026-08-20

### Fixed

- Animated collider captures are reused only while their evaluated geometry,
  transforms, sampling plan, and solver-space export contract still match.
- Geometry diagnostics now present all mapped intersections and degenerate
  faces together instead of hiding valid findings behind single-item
  navigation.
- Auto Fix repairs every safely supported diagnostic in one undoable action,
  reports Blender status-bar progress, and never starts a Bake implicitly.
- Auto Fix safely leaves the Edit Mode used by degenerate-face preflight before
  validating and changing source geometry.
- Bounded intersection corrections now fail closed when the confirmed faces
  would still intersect, instead of reporting success and exposing additional
  crossings.
- The production Simulation panel groups the diagnostic summary, primary Auto
  Fix action, and independent Clear action into a compact visual hierarchy.

### Validation

- Full Python suite: 1,431 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS built and validated the Windows extension locally; all 3
  packaged-artifact tests and the forbidden-solver-material scan passed.
- Added regression coverage for animated collider cache reuse and invalidation,
  combined diagnostic rendering, multi-issue repair, and Auto Fix progress.
- Real Blender/Lumen validation covers the updated Top-and-Shorts test scene,
  including isolated Shorts intersection mapping and post-fix revalidation.
- The external PPF Contact Solver remains unbundled and unmodified.

## 2.2.42 - 2026-08-19

### Fixed

- Re-Bake now preserves the previous successful playback generation until a
  separately written and validated successor is committed.
- Live Bake progress no longer retargets an existing successful Mesh Cache to
  a growing private PC2 file.
- Playback attachment reuses the owned modifier transactionally and rolls back
  filepath, visibility, stack position, ownership, and Bake metadata when the
  new generation cannot be committed.
- Locked obsolete PC2 files are treated as bounded deferred garbage and cannot
  fail an otherwise successful Re-Bake or Clear operation.
- Active playback paths and artist-created Mesh Cache modifiers are excluded
  from cleanup and retargeting.
- Multi-object playback attachment validates every cache before switching and
  rolls all objects back if any commit fails.
- Auto Fix object lookup now skips scene objects without persistent Cloth NeXt
  identity, and Bake-window diagnostic metadata names the actually affected
  object instead of the first deformable.
- Animated-collider cleanup now refreshes the dependency graph through the
  valid Blender context.

### Validation

- Full Python suite: 1,419 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS real lifecycle test passed with a Windows-locked obsolete
  PC2, owned-modifier retargeting, Clear, Unicode paths, and artist-cache
  preservation.
- GitHub CI tests and Blender smoke checks passed on the merged fix PR.
- Added fault-injection coverage for permanent cleanup failures, commit
  rollback, repeated generations, active-cache safety, and multi-object
  coherence.

## 2.2.41 - 2026-08-18

### Fixed

- Self-intersection validation now retains one authoritative diagnostic result
  from solver payload through source-face mapping, UI counters, navigation, and
  viewport rendering.
- Solver-reported totals no longer disappear when only part of the payload can
  be mapped; the UI explicitly distinguishes detected, mapped, and unmapped
  intersections.
- Valid-but-different solver triangle index spaces are verified against the
  solver-provided triangle geometry before Blender ownership is assigned.
- Degenerate faces are retained as first-class diagnostics and rendered with
  face, outline, and point primitives so collapsed triangles remain visible.
- Clearing, starting another Bake, loading another file, and unregistering the
  add-on now retire the complete diagnostic session and its draw handlers.
- Managed solver sidecars and status payloads now normalize supported nested,
  serialized, and enveloped violation formats without silently losing records.

### Validation

- Full Python suite: 1,411 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS source registration smoke test passed.
- Added regression coverage for detected-versus-mapped accounting, unmapped
  warnings, degenerate primitives, handler lifecycle, recovery-sidecar lookup,
  payload normalization, and stale diagnostic reset paths.

## 2.2.40 - 2026-08-18

### Fixed

- Intersection diagnostics and **Auto Fix Intersections** now render in the
  `Simulation` panel that is actually visible for Cloth, Rod, Soft Body, Rigid
  Body, Collider, and Force roles.
- Corrected the 2.2.39 hotfix, which targeted a legacy `Solver` panel whose
  visibility poll excludes production simulation roles.

### Validation

- Added a regression that polls and draws the real production `Simulation`
  panel for a Cloth object and verifies the Auto Fix operator is present.
- Production Bake UI suite: 45 passed.
- Full Python suite: 1,395 passed, 9 skipped, 3 deselected.

## 2.2.39 - 2026-08-18

### Fixed

- Intersection diagnostics and **Auto Fix Intersections** now render in the
  normal production Solver panel after a solver-confirmed intersection error.
- The hidden Developer Tools view reuses the same diagnostics renderer instead
  of owning the only path to the controls.

### Validation

- Added production-panel regression coverage for mapped self-intersections and
  the enabled Auto Fix action.
- Full Python suite: 1,394 passed, 9 skipped, 3 deselected.

## 2.2.38 - 2026-08-18

### Added

- Added a conservative **Auto Fix Intersections** action for fully mapped,
  solver-confirmed initial cloth self-intersections.
- Corrections separate both triangle surfaces, accumulate and average shared
  vertex contributions, and clamp movement relative to local triangle scale.

### Fixed

- Intersection overlay clearing now removes GPU handlers and retained geometry
  idempotently, including add-on shutdown and repeated set/clear cycles.
- Diagnostics now reject non-drawable triangle data and distinguish the solver's
  detected count from the number of intersections mapped for display.

### Safety

- Auto Fix rejects stale topology, geometry, transforms, shape keys, linked
  meshes, generated proxies, colliders, rods, sentinels, and incomplete mappings.
- The edit preserves topology, supports Blender Undo, and starts the normal Bake
  workflow so the existing solver remains authoritative after correction.

### Validation

- Added focused regression coverage for overlay lifecycle, drawable geometry,
  deterministic separation, shared-vertex accumulation, correction clamping,
  unsupported classifications, and stale mapping safeguards.
- Full Python suite: 1,393 passed, 9 skipped, 3 deselected.

## 2.2.37 - 2026-08-17

### Changed

- Cropped the approved color and monochrome Cloth NeXt logo sources exactly to
  their visible alpha bounds without changing their visible pixels or colors.
- Refreshed the canonical add-on logo from the cropped color source and rebuilt
  the Companion PNG and Windows ICO derivatives.
- Companion identity generation now preserves the source aspect ratio while
  fitting the logo into the required square application-icon canvas.

### Validation

- Added regression coverage for exact cropped dimensions, edge-tight alpha
  bounds, canonical logo equality, aspect-preserving output, and deterministic
  Companion PNG/ICO generation.

## 2.2.36 - 2026-08-17

### Changed

- Replaced the add-on brand asset and Companion application identity with the
  new approved full-color Cloth NeXt logo.
- Companion PNG and Windows ICO derivatives are now generated deterministically
  from the canonical high-resolution color logo.
- Preserved the dedicated white transparent Blender UI icon family for reliable
  contrast in Blender's dark theme.

### Validation

- Added regression coverage that verifies the generated Companion identity is
  pixel-identical to the approved color source at its runtime size while all
  Blender runtime icons remain white and transparent.

## 2.2.35 - 2026-08-15

### Fixed

- Recovery cleanup now derives every removable checkpoint path from Cloth NeXt's
  owned recovery root instead of trusting persisted paths.
- Blender validation handlers remain registered across `.blend` file loads, and
  cache reuse now includes the resolved solver release identity.
- Recovery sessions can transition cleanly from a confirmed checkpoint to an
  abandoned state, and missing worker status preserves the more actionable
  installation or quarantine error.
- Companion status polling no longer blocks Blender's animation loop while no
  transport message is ready.

### Validation

- Added real-Blender lifecycle and solver-identity reuse/invalidation harnesses,
  expanded smoke teardown checks, and regression coverage for recovery ownership,
  handler persistence, cache identity, and non-blocking Companion polling.
- Full Python suite: 1,379 passed, 9 skipped, 3 deselected; all 9 external solver
  integration tests passed separately with the official Lumen solver.

## 2.2.34 - 2026-08-15

### Fixed

- The Bake window now uses a passive native Windows topmost Z-order while a
  Bake is active, keeping progress visible without repeatedly stealing focus.
- Animated Collider captures are reused from the verified export cache when
  geometry, animation, transforms, timing, capture settings, and safe
  dependencies are unchanged.
- Solver project-build progress is transported as generic percentage progress
  instead of simulation-frame progress, so the Bake window shows `43%` rather
  than the misleading `Frame 43 / 100` during contact construction.

### Safety

- Missing, incomplete, corrupt, or unverifiable Collider cache artifacts force
  a complete recapture; uncertain dependencies remain fail-closed cache misses.
- BUILDING percentages no longer seed simulation-frame ETA estimation.

## 2.2.33 - 2026-08-14

### Added

- Corrective Smooth can now participate in deformable solver-input geometry,
  both after an Armature and as the only supported input modifier.
- Cloth NeXt playback is inserted after the final Armature or Corrective
  Smooth solver-input modifier so corrected deformation is not applied twice.

### Safety

- Solver-input modifiers after a topology-changing modifier are rejected with
  an actionable validation error, while constant vertex-count checks remain in
  force for export and animated Pin capture.
- Temporary modifier visibility changes are restored after successful and
  failed capture, and disabled Corrective Smooth modifiers remain excluded.

## 2.2.32 - 2026-08-14

### Fixed

- The Bake window now launches before expensive scene validation, topology
  hashing, evaluated geometry capture, and run-plan construction.
- Clicking Bake therefore provides immediate visible feedback while Blender
  continues preparing complex scenes in the background.

## 2.2.31 - 2026-08-14

### Fixed

- Periodic Lumen Recovery checkpoints are now discovered from the solver's
  atomic state files when its normal status response omits `saved_states`.
- The configured checkpoint interval now produces artist-visible, verified
  Recovery points during a running Bake instead of appearing only after
  Save on Cancel.

## 2.2.30 - 2026-08-13

### Fixed

- Live Bake auto-framing now animates continuously at a time-based refresh
  cadence instead of stepping once per completed solver frame.
- Cinematic framing suppresses small evaluated-bound fluctuations and eases
  both inward and outward motion instead of snapping the viewport backward.
- Recovery now remains enabled when a complex evaluated scene cannot use the
  optional persistent export cache; its canonical solver Scene hash provides
  the durable Recovery project identity.

## 2.2.29 - 2026-08-12 (Dev channel)

### Fixed

- Cancel during solver simulation now produces a durable, visible Recovery
  checkpoint and Resume successfully continues the same solver project.
- Recovery identity is derived from the canonical encoded solver parameters,
  preventing false material/settings mismatch errors on immediate Resume.
- Checkpoint discovery tolerates delayed disk publication after the solver
  status connection closes.
- Rebake safely accepts owned private live caches and only the exact Recovery
  partial authenticated by the active run plan.
- Timeline marker and Bake strip follow simulation and fetch progress while
  retaining the required attach-before-frame-evaluation live-loading order.

## 2.2.28 - 2026-08-12 (Dev channel)

### Added

- Added optional live Bake viewport auto-framing with configurable margin and
  Smooth or Cinematic motion. Fast cloth motion pulls the view back
  immediately to keep every deformable visible while closer framing remains
  softly damped.

### Fixed

- Cancel requests issued during the transition into export remain latched and
  reliably reach the Bake worker.
- Cancelled Bakes retain their terminal state so the Bake Window and Recovery
  UI can observe saved checkpoints before a new Bake or Resume takes ownership.

## 2.2.27 - 2026-08-12 (Dev channel)

### Added

- Added the verified Lumen solver profile using PPF protocol 0.18 and schema 2.
- Added exact Lumen release identity, archive size, SHA-256, frontend overlay
  verification, crash classification, and protocol-specific parameter encoding.

### Changed

- Velune protocol 0.13 remains the stable default while Lumen protocol 0.18 is
  available as the current compatible release.
- Solver Preferences now expose and download only Velune and Lumen; retired
  protocol 0.11 installations are no longer shown or selectable.
- Recovery startup validates the control server independently from the status
  of the deliberately interrupted project before resuming that project.

### Fixed

- Recovery can resume an interrupted Lumen Bake from its latest verified
  checkpoint without uploading or rebuilding the scene.
- Multiline solver errors and `crash_kind` now survive status parsing and are
  included in artist-facing and diagnostic failure reports.
- Protocol 0.18 no longer receives the removed `ccd-reduction` and
  `ccd-max-iter` parameters.

## 2.2.26 - 2026-08-10 (Dev channel)

### Changed

- The Bake Window uses the dedicated Contacts, Newton Steps, and Linear
  Iterations artwork in opaque white at 16 px, without changing the status-bar
  dimensions or layout.
- Wind Variation now produces separated positive gusts above a stable base
  wind instead of repeatedly weakening the flow below its configured value.
  This reduces continuous cloth flutter at high Wind, Variation, and Noise
  Scale settings.

## 2.2.25 - 2026-08-10 (Dev channel)

### Added

- Wind Variation now has a Noise Scale control directly below it. The value is
  expressed as a time scale: higher values create slower, broader gusts, while
  lower values create faster changes.

### Changed

- Noise Scale participates in cache fingerprints, so changing gust timing
  reliably marks an existing Bake stale.

## 2.2.24 - 2026-08-10 (Dev channel)

### Changed

- Wind Variation now combines deterministic slow pressure drift with shorter
  smooth gusts instead of repeating two uniform sine rhythms.
- Gusts remain reproducible per Force object and bounded by the configured
  Wind Variation value.

## 2.2.23 - 2026-08-10 (Dev channel)

### Fixed

- Live Bake now flushes each completed frame before notifying Blender and
  attaches the private growing PC2 file instead of waiting for the final
  transactional cache publication.
- The timeline advances only after its corresponding deformation data is
  readable, including during first bakes and multi-object simulations.

## 2.2.22 - 2026-08-09 (Dev channel)

### Added

- Gravity direction can be selected explicitly from the six world axes and is
  independent of the Force Empty rotation.

### Changed

- Wind remains aligned to the Force Empty's local positive Z axis.
- New Force objects use the solver-recommended Air Density of `0.01`; the UI
  warns when active Wind is paired with an effectively invisible density.

### Fixed

- Live Bake now attaches each growing PC2 cache before advancing the timeline,
  so newly baked deformation is visible immediately, including on a first run.

## 2.2.21 - 2026-08-09 (Dev channel)

### Changed

- Force controls are presented as one permanent list without an active Force
  Type dropdown; Gravity and Wind now have independent strengths and can act
  simultaneously alongside all aerodynamic controls.
- New Force objects default Wind and Wind Variation to zero so their initial
  state cannot create an unintended gust.
- Object Type menu entries use distinct semantic icons and role-specific hover
  descriptions.
- Nested Pin, Constraint, Collision Timing, Contact, and Motion panels use
  semantic header icons instead of the generic Cloth NeXt mark.

### Release preparation

- Version and solver compatibility metadata advance together to 2.2.21.
- This Dev release establishes the UI-consistency baseline for the upcoming
  2.3.0 Beta milestone.

## 2.2.20 - 2026-08-09 (Dev channel)

### Added

- Viewport role colors distinguish Cloth, Colliders, Rods, Soft Bodies, Rigid
  Bodies, and Forces while preserving each object's previous display color.
- Live Bake progress advances Blender's timeline to the newest completed frame.

### Changed

- Force objects expose all configurable values in one unified panel.
- Friction retains its existing artist-facing UI but maps to half the solver
  value for more controllable object contact.
- The collapsible Bake Companion Details area uses grouped run and solver stats
  in place of the performance graph, while retaining ETA.
- Cloth NeXt UI actions use the bundled Cloth NeXt icon set consistently.

### Fixed

- Interrupted Bake recovery no longer appends duplicate PC2 samples.

## 2.2.19 - 2026-08-09 (Dev channel)

### Added

- Permanent deformation controls for Cloth, Cable / Rope, and Soft Body are
  translated to their native PPF plasticity parameters.
- Advanced Pin Motion supports multiple independent Pin Groups, animated
  targets, and per-group pull strength while preserving Collider contact.
- Soft Constraints provide separate Target, transform channel, and Strength
  rows without a redundant enable switch.
- Collision Timing, Advanced Contact Distance, and Advanced Contact Solver
  expose audited PPF controls with artist-facing names and expert warnings.
- Motion Overrides can replace an object's world-space Move or Spin velocity
  on a selected Blender frame.

### Changed

- Newton and its Live Preview, solver selector, Preferences download flow, and
  isolated runtime have been removed. Cloth NeXt again uses the established
  PPF-only Bake workflow.
- Animated and deforming Collider export remains responsive in the Bake window,
  and cache recovery can reuse a compatible export.

### Fixed

- Changing a Pin constraint between Soft and Hard invalidates the exported mesh
  so the next Bake cannot reuse incompatible Pin data.
- PPF Pin groups, soft pulls, Collider timing, and cache fingerprints now retain
  every solver-visible setting across Rebake and Recovery.

## 2.2.18 - 2026-08-08 (Dev channel)

### Fixed

- Bake ownership is now reserved process-wide before either backend starts the
  Companion. Duplicate loaded add-on generations can no longer race the same
  Bake window or terminate a valid Newton job with `CNX-E110`.
- A rejected stale PPF callback cannot release or modify the active Newton
  reservation. The reservation is released on preparation errors and normal
  terminal Bake states so Rebake remains available.

## 2.2.17 — 2026-08-08 (Dev channel)

### Fixed

- A stale PPF animated-Pin or Collider capture timer can no longer overwrite a
  newer Newton Bake job, open the Bake window under the wrong job ID, or abort
  Newton immediately with `CNX-E110`.
- PPF preparation and startup callbacks now verify ownership of the shared
  Bake controller before publishing status or continuing into solver startup.
- Cancelling an orphaned active Bake state without a worker now reaches the
  terminal Cancelled state and releases the Bake lock.

## 2.2.16 — 2026-08-08 (Dev channel)

### Fixed

- Newton scene preparation now starts only after the common Bake window is
  visible and advances cooperatively through Blender timers. Animated and
  deforming Collider sampling is shown in the Bake window instead of freezing
  Blender before the window opens.
- Newton uses Blender's scene gravity when no Cloth NeXt Gravity Force exists,
  including disabled scene gravity.
- Gravity-related Newton diagnostics without a Force object no longer get
  misclassified as `CNX-E108`.

## 2.2.15 — 2026-08-08 (Dev channel)

### Added

- Newton is a first-class selectable backend in the normal offline Bake
  workflow: select Solver, select Quality, Bake, follow the common Bake window,
  and receive an attached PC2 cache.
- Newton VBD now supports mixed Cloth, Soft Body, and Rigid Body scenes together
  with static, animated, and deforming stable-topology Colliders.
- Soft Body surfaces are tetrahedralized in the isolated Newton environment by
  pinned `pytetwild`/fTetWild 0.3.0 and mapped back to the original Blender
  surface for cache playback.

### Changed

- Low, Medium, High, Extreme, and Custom quality resolve to Newton-native VBD
  substeps and iterations rather than reusing PPF parameters.
- Live Preview is no longer exposed or registered; Newton uses only the normal
  production Bake lifecycle.

### Fixed and validated

- Native Newton/fTetWild diagnostics are isolated from the framed worker
  protocol, preventing Soft Body Bake startup from hanging.
- fTetWild tetrahedron orientation is normalized for Newton before model build.
- Real Newton 1.4.0 / Warp 1.15.0 CUDA tests cover Cloth pins and sag,
  self-collision, static collision, deforming Collider sampling, animated pins,
  and a coupled Cloth/Soft Body/Rigid Body scene.

## 2.2.14 — 2026-08-02 (Dev channel)

### Fixed

- Large Newton scenes no longer fail with `Newton worker message exceeded the
  protocol limit`. Production preview and Bake requests are transferred as an
  atomic local scene artifact while the bounded JSON protocol carries only a
  compact descriptor.

### Safety and validation

- The worker accepts a scene artifact only from its declared owned session
  directory and verifies its exact name, bounded size, and SHA-256 before
  parsing it. Missing, replaced, truncated, oversized, or foreign artifacts
  fail closed.
- Real Newton/CUDA and Blender gates cover the artifact transport together
  with Multi-Cloth, deforming Colliders, and animated Pins.

## 2.2.13 — 2026-08-02 (Dev channel)

### Added

- Newton Live Preview and experimental Newton Bake now support `Follow
  Animation` pins for every Cloth object in a shared solve.
- Evaluated pin targets are captured per frame and interpolated per Newton
  substep with matching kinematic velocity, while Static pins retain their
  existing fixed behavior.

### Safety and validation

- Animated Pin tracks validate frame counts, target counts, finite positions,
  Cloth ownership, and topology before worker startup.
- Real Newton/CUDA and Blender gates verify multiple Cloth objects, a
  deforming Collider, animated Pins, final target accuracy, and cleanup.

## 2.2.12 — 2026-08-02 (Dev channel)

### Fixed

- Starting Newton Live Preview no longer evaluates every animated Collider
  frame synchronously inside the UI property callback. Blender regains control
  immediately and captures one evaluated frame per main-thread timer step.
- Animated Collider capture now reports object, frame, and overall progress;
  cancelling during capture closes the iterator and restores the original
  timeline frame without starting a worker.

## 2.2.11 — 2026-08-02 (Dev channel)

### Fixed

- Deforming quad and ngon Colliders no longer fail Newton capture merely
  because Blender chooses a different loop-triangle diagonal at another frame.
  Stable evaluated polygon loops remain the authoritative topology check while
  every sample uses the initial deterministic triangle ordering.

### Interface

- The Bake selector is now labelled `Solver` and presents the product-facing
  choices `Production (Lunelle)` and `Preview (Principia)` without changing
  the stored `PPF` and `NEWTON` identifiers.
- The Newton Live Preview toggle is icon-only: Play while inactive and Pause
  while active.
- Live Preview controls are hidden while `Production (Lunelle)` is selected
  and appear only for `Preview (Principia)`.

## 2.2.10 — 2026-08-02 (Dev channel)

### Added

- Newton Live Preview and experimental Newton Bake now support multiple Cloth
  objects in one shared simulation, with separate non-destructive preview and
  PC2 playback outputs for every Cloth.
- Newton now samples animated and deforming triangle Colliders across the Bake
  range, interpolates their motion per solver substep, and refits collision
  acceleration data without rebuilding the simulation model.

### Safety and validation

- Animated Collider topology is required to remain constant and fails closed
  before worker startup when it changes.
- Added real Newton/CUDA and Blender gates covering two Cloth objects,
  deforming Collider samples, viewport result splitting, and source restoration.

## 2.2.9 — 2026-08-02 (Dev channel)

### Fixed

- Newton Preview registration no longer accesses `bpy.data.objects` while
  Blender exposes its restricted registration context. Orphaned preview data
  is cleaned on a deferred main-thread timer once normal data access becomes
  available.
- Newton cleanup and add-on unload tolerate Blender's restricted data state
  without preventing the extension from enabling.

## 2.2.8 — 2026-08-02 (Dev channel)

### Added

- Experimental Newton 1.4.0 integration with a compact, confirmation-gated
  installer, isolated external Python environment, and the Principia codename.
- Non-destructive Newton Live Preview with play, pause, scrubbing, rewind,
  static colliders, hard static pins, self-contact, VBD, and experimental
  Style3D support.
- Experimental Newton offline Bake through the existing Cloth NeXt Bake
  action, producing verified atomic PC2 playback caches without changing the
  default PPF backend.
- Persistent per-object mesh capture reuse and developer performance metrics
  for Newton preview and Bake preparation.

### Fixed

- Recovery discovery now survives reopening a `.blend` and keeps checkpoint
  compatibility provisional until the current Bake identity is recomputed.
- Resume remains bound to the selected durable solver project even when the
  conservative scene-export cache key changes across a Blender restart.
- Missing, corrupt, incompatible, or replaced Recovery metadata now fails
  closed instead of silently starting a fresh Bake.
- The real artist-path Recovery gate now proves same-project resume, rejects
  scene upload or project rebuild, and requires the solver's `--load=-1`
  command after a hard Blender abort.

### Known limitations

- Newton support is experimental and currently targets one Cloth object with
  static colliders. Newton Bake does not yet provide Recovery checkpoints.
- The external PPF Contact Solver remains the default production backend and
  is never bundled with Cloth NeXt.

## 2.1.18 — 2026-07-28 (Dev channel)

### Fixed

- The Bake panel now recognizes the explicitly selected side-by-side solver
  installation instead of checking only the legacy single-solver pointer.
- The Bake button tooltip now describes the artist-facing action instead of
  exposing an internal startup-gate implementation detail.

## 2.1.17 — 2026-07-28 (Dev channel)

### Fixed

- Confirming a protocol 0.13 solver download now retains the selected release
  ID instead of switching back to the default protocol 0.11 installer and
  losing the confirmation state.

## 2.1.16 — 2026-07-28 (Dev channel)

### Fixed

- Solver download and external-installation selection now use package-relative
  imports under Blender's `bl_ext.<repository>.cloth_next` namespace instead
  of assuming a top-level `cloth_next` package.

## 2.1.15 — 2026-07-28 (Dev channel)

### Added

- Side-by-side management for official and external PPF Contact Solver
  installations, including an explicit active-solver selector in Add-on
  Preferences.
- Verified managed download support for the official protocol 0.13 / schema 2
  Windows release while preserving protocol 0.11 / schema 1.
- Per-installation health, release, protocol, schema, path, download,
  reinstall, removal, and external-registration controls.

### Changed

- Bake, Update Params, diagnostics, and Recovery now resolve one immutable
  solver installation and route its executable, frontend, protocol profile,
  schema encoder, and metadata together.
- Managed releases use independent immutable installation directories and
  atomic verified publication.
- Recovery metadata includes the exact solver installation and release
  identity to prevent checkpoint reuse with a different solver release.

### Fixed

- Protocol 0.13 scene and parameter encoding now uses schema 2 envelopes,
  frame offsets, floating-point FPS, and the required time scale.
- Protocol-specific overlays prevent the protocol 0.11 frontend integration
  from being applied to protocol 0.13 or unknown releases.
- Failed downloads, verification, extraction, overlay, and health checks
  preserve every existing installation and the active solver selection.

## Unreleased

### Added

- Bake diagnostics now log the effective Blender gravity at the first Bake
  frame and every frame where it changes, making delayed or animated gravity
  immediately visible in the Cloth NeXt log.
- Role-specific Physics Properties workflows for Cloth, Cable / Rope,
  Soft Body, Rigid Body, Collider, and Force.
- Compact scene statistics below the global Bake action.

### Changed

- Cloth NeXt Physics panels now follow the shared Setup, Simulation, Material,
  Shape, Collision, and Advanced structure while showing only controls that
  apply to the selected object role.
- Bake progress, frame timing, ETA, and hardware details remain in the
  dedicated Cloth NeXt Bake window instead of being duplicated in Blender.
- The main Physics panel shows the installed version in its title and adds a
  compact warning only when an add-on update is available.
- Cable / Rope and artist-facing Sideways Response terminology replace
  internal implementation names in the visible UI without changing solver
  mappings or stored enum identifiers.
- Quality preset explanations are provided as button hover tooltips.

### Fixed

- Physics subpanels use their existing semantic Cloth NeXt icons instead of
  falling back to the generic information icon.
- Collider proxy estimates shown during panel drawing use cached counts and
  avoid evaluating meshes.
- Missing or quarantined native solver workers are now identified before
  simulation, with a direct recovery hint instead of a misleading generic
  solver-connection failure.

## 2.1.0 — 2026-07-22 (Beta channel)

### Added

- PDRD Rigid Body simulation with artist-facing material controls, shared
  Cloth interaction, and per-object Bake ranges.
- Per-vertex-group Friction overrides while unassigned vertices retain the
  object's general Friction value.
- Blender-style Cloth Sewing: when enabled below Shrink, mesh edges unused by
  any face become PPF stitch constraints with adjustable Sewing Strength.
- Solved seams that enter the contact range are closed exactly in the playback
  cache without changing its vertex count, eliminating visible micro-gaps.
- Sewing validation selects face-less, massless stitch vertices in Edit Mode
  before they can cause a singular solver system.
- In-panel installed-version and update status backed by the selected GitHub
  update channel, plus an explicit repository-registration action.
- Randomized Wind variation around the configured strength.

### Changed

- The managed solver manifest now offers the verified official
  `2026-07-13-21-05` Windows release. Older managed releases are shown as an
  available solver update even though the internal package version remains
  `0.1.0`.
- Modifier capture exports the intended pre-simulation mesh state and places
  playback after the last enabled Armature modifier when present.
- Cloth NeXt continues Blender's two-column Physics grid at half width and
  uses the white Cloth NeXt logo in its add/remove button.
- Solver-reported self-intersections now produce a concise artist-facing error
  instead of exposing the complete process tail in Blender.

### Fixed

- Multi-object Bake range validation and synchronization now include every
  enabled deformable, including Rigid Bodies.
- Rigged deformables export their Bake-start pose rather than the current
  viewport frame.
- Playback-cache attachment remains stable when modifier ordering changes.
- Several solver startup, status, cache-import, and cancellation failures now
  preserve a precise stage and actionable error code.

## 2.0.0 — 2026-07-19 (Stable channel)

### Added

- Rod, Soft Body, and multi-object Cloth workflows alongside the established
  Cloth and Collider simulation roles.
- A categorized library of 37 fabric presets, including 30 research-backed
  starting points derived from the MIT Fabric Properties Dataset.
- Animated Collider sampling, opt-in generated Collider proxies, animated
  Forces, Scene Health, authenticated cache recovery, privacy-safe support
  reports, solver telemetry, ETA, and per-frame performance history.

### Changed

- Artist-facing controls and documentation consistently use practical terms,
  including **Friction**, while retaining exact PPF mappings in supporting
  technical context.
- The Bake Companion follows preparation through simulation in one window and
  keeps its performance graph, centered ETA, cancellation controls, and error
  details within the fixed compact layout.
- Error recovery actions can be refreshed from the public Cloth NeXt error
  directory without uploading scene data or diagnostics; every installed build
  retains an offline fallback.

### Fixed

- Collider Proxy discovery no longer writes Blender data during panel drawing,
  generated reduction follows the requested target more closely, and animated
  preparation keeps Blender responsive between captured frames.
- Multi-object cache publication is transactional, update handoff tests are
  deterministic, and Companion/solver failure paths preserve actionable CNX
  codes and local diagnostics.

### Release

- Promotes the successfully published and tested 1.2.0 Beta line to the 2.0.0
  Stable channel. The release updates Stable, Beta, and Dev repositories with
  one byte-identical verified ZIP.

## 1.2.0 — 2026-07-19 (Beta channel)

### Changed

- Artist-facing collision terminology now uses **Friction** instead of
  **Surface Grip** while retaining the existing internal property for scene
  compatibility.
- Material, collision, Rod, Soft Body, solver-quality, support, and marketplace
  copy now leads with practical effects and keeps technical PPF mappings in
  supporting tooltip context.
- The redundant Overview panel was removed from Physics Properties.
- Release tooling and documentation consistently use plain tags such as
  `1.2.0` without a leading `v`.

### Fixed

- The Blender update-handoff smoke test no longer depends on live repository
  responses and now completes deterministically while retaining real Blender
  repository, RNA, registration, and operator coverage.
- Required update smoke tests have an explicit timeout so an external or future
  regression cannot occupy a CI runner for six hours.

### Documentation

- Superhive product copy, FAQ, embedded documentation, and support instructions
  now match the current add-on UI and privacy-safe reporting workflow.

## 1.1.0 — 2026-07-19 (Beta channel)

### Added

- A categorized library of 37 fabric presets, including 30 research-backed
  starting points derived from the MIT Fabric Properties Dataset.
- Animated Collider sampling controls and opt-in generated simulation proxies.
- Scene Health, authenticated cache recovery, privacy-safe support reports,
  solver telemetry, remaining-time estimates, and per-frame performance
  history in Bake Details.

### Changed

- Animated Pin and Collider preparation yields to Blender between frames and
  performs less repeated dependency-graph and mesh work.
- The Bake window remains continuous from preparation through simulation;
  its Details view gives the performance graph the full panel with a centered
  ETA while preserving the bottom controls.
- Multi-object cache publication is transactional across every playback target.

### Fixed

- Collider Proxy discovery no longer writes Blender ID properties during panel
  drawing, and generated reduction performs bounded follow-up passes toward
  the requested vertex ceiling.
- Companion startup, solver early exits, Pin-capture failures, cancellation,
  and Blender-state restoration retain actionable diagnostics.

## 1.0.9 — 2026-07-18 (Dev channel)

### Fixed

- Animated preparation now waits for the Companion's Tk event loop to report
  ready before evaluating the first expensive Blender frame.
- Pin and Collider preparation yields briefly between frames so Blender window
  events, Companion IPC, redraw and Escape cancellation remain responsive.
- Animated-Collider-only Bakes use the same asynchronous preparation gate
  instead of blocking Blender immediately after launching the Bake window.

## 1.0.8 — 2026-07-18 (Dev channel)

### Added

- Categorized, hover-opened Material Preset menus with a 37-material library.
- Thirty research-backed fabric starting points derived from Bouman et al.'s
  MIT Fabric Properties laboratory measurements, with explicit provenance and
  conversion documentation.

### Changed

- Animated Follow Animation Pin capture reuses one evaluated dependency graph
  per frame, precomputes pin-index arrays, and removes the artificial timer
  delay between frames.
- Force animation is sampled during Pin capture instead of walking the complete
  frame range a second time afterward.
- Animated Collider and Force capture no longer repeat Blender dependency-graph
  updates already performed by `scene.frame_set()`.

### Fixed

- The preparation stage now switches from completed Pin progress to explicit
  animated-Collider or evaluated-geometry progress instead of appearing stuck
  at values such as `1000 / 1000`.

## 1.0.7 — 2026-07-17 (Dev channel)

### Changed

- System Load uses restrained monochrome traces, a neutral left accent, and no
  outer frame; only the RAM Auto Cancel threshold remains red.
- Animated Pin capture now owns a modal wait state with a wait cursor and
  explicit Escape cancellation so Blender cannot be edited during capture.

### Fixed

- The Bake Companion is centered before it first becomes visible and no longer
  appears at the top-left before jumping into place.
- Expanded Bake Details reserve their requested height so action buttons remain
  visible instead of being pushed against the bottom edge.
- Owned solver connection failures preserve early-exit evidence and process
  output, making `CNX-E141` timeouts substantially more actionable.

## 1.0.6 — 2026-07-17 (Dev channel)

### Added

- Bake Details now include an estimated time to finish and link documented
  `CNX-E…` failures directly to the public error-code reference.
- The solver activity line surfaces useful PPF runtime progress instead of a
  generic advancing-simulation message.
- Non-blocking warnings identify unusually large animated-Collider captures
  and destabilizing high Collider Gap plus Surface Grip combinations.

### Changed

- Animated Follow Animation Pin capture now evaluates frames sequentially,
  suspends owned playback once per capture, and reads evaluated coordinates in
  bulk without allocating a complete temporary mesh for every frame.
- The Bake Companion stays in one window across export and simulation, while
  System Load visuals now follow the public website's mint-on-dark palette.
- Bake Details expose the remaining-time estimate without changing the compact
  default window layout.

### Fixed

- Pin-capture cancellation, add-on shutdown, and capture failures restore the
  artist's frame and playback flags deterministically.
- Animated Pin capture failures now produce a visible console message, stable
  error code, and persistent Bake diagnostics instead of silently ending a
  Blender timer.
- Runtime activity parsing tolerates partial solver output and retains the
  latest useful solver stage.

## 1.0.0 — 2026-07-16 (Stable channel)

### Added

- First Stable release of the production Cloth NeXt Bake workflow.
- Multi-object Cloth, Rod / Cable, and Soft Body Bakes, including Follow
  Animation Pins and colliderless projects.
- Keyframeable Empty Forces for every PPF force parameter exposed by Cloth NeXt.
- Seventy-four stable, documented Bake error codes with persistent local
  diagnostics and atomic per-run failure reports.

### Changed

- Release repositories now use cumulative visibility: Stable publishes to
  Stable, Beta, and Dev; Beta publishes to Beta and Dev; Dev remains Dev-only.
- Companion transport failures remain visible, pulse red, and require explicit
  user acknowledgement instead of silently closing.
- Authenticated Companion status messages are size-bounded and tolerate unknown
  future enum values without crashing the UI.

### Fixed

- RAM safety cancellation now terminates as actionable `CNX-E166` instead of
  losing its cause in a generic cancellation state.
- Worker tracebacks are published atomically, full Bake failures persist in a
  rotating `bake-errors.log`, and IPC publication can no longer mask the
  original solver error.
- Channel validation accepts more-stable candidates while still rejecting any
  less-stable candidate and ambiguous repository index.

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
- Companion failures show documented `CNX-E…` codes, pulse red, and remain
  open for explicit acknowledgement through the title-bar close control.
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
