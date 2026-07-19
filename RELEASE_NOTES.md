# Cloth NeXt 1.1.0

Cloth NeXt 1.1.0 brings the current Dev line together for the next marketplace
build. It expands material choice, improves animated preparation and Collider
workflows, and strengthens Bake diagnostics, cache safety, and responsiveness.

- Animated preparation now opens and verifies the Bake window before the first
  heavy frame evaluation, and yields between frames to keep Blender responsive.
- Animated-Collider-only Bakes share the asynchronous preparation path, while
  early Companion exits become visible preparation errors instead of a missing
  window beside an apparently frozen Blender process.

- The Material panel now opens a categorized hover library with 37 fabric
  starting points, including 30 presets grounded in laboratory area-weight and
  bending measurements from the MIT Fabric Properties Dataset.
- Research-backed presets retain their source sample and measured values; the
  density conversion and solver-specific bending calibration are documented
  without presenting calibrated stretch or friction as lab measurements.

- System Load is quieter and easier to read: monochrome graphs, a neutral
  accent, no outer frame, and red reserved for the RAM safety limit.
- The Companion is centered before its first visible frame and expanded Details
  now keep all controls comfortably inside the window.
- Animated Pin capture locks interactive input behind a wait cursor and allows
  explicit Escape cancellation while capture is active.
- Solver connection failures now retain owned-process exit evidence and output
  so `CNX-E141` reports identify early solver termination more reliably.

- Follow Animation Pin capture is substantially faster on rigged characters:
  frames are evaluated sequentially, cache playback is suspended once, and
  evaluated coordinates are read in bulk without per-frame mesh copies.
- Pin-capture failures now restore Blender state and surface a persistent error
  code in the Companion, logs, and System Console.
- Bake Details show an estimated time to finish, useful PPF runtime activity,
  and a direct link to the matching public CNX error documentation.
- The same Companion window now remains open from preparation through the
  actual solve, and its System Load presentation matches the website palette.
- Large animated-Collider captures and risky high Gap plus Surface Grip
  combinations produce clear non-blocking warnings before a Bake.
- Animated Colliders now default to eight evaluated samples per frame and can
  be adjusted from 2–32 for fast or strongly curved motion.
- Multi-object cache publication includes a shared scene identity and rolls
  playback modifiers back if any target fails to attach.
- PPF contact diagnostics retain last, peak and sample counts in logs and
  authenticated cache metadata.
- Scene Health preflight reports validation, storage, RAM, scope and animated
  Collider sampling risks before a long Bake.
- Cache Recovery inventories owned PC2 pairs and removes only authenticated
  partial or corrupt results; legacy unverified PC2 files remain untouched.
- Privacy-safe support reports redact object names and filesystem locations and
  include versions, resource state, error code and available contact metrics.
- The public Dev update channel requires its explicit risk acknowledgement but
  no longer requires the unrelated Developer Tools UI toggle.
- A generated and headless-validated Superhive Quick Start scene, product-page
  draft, FAQ and review checklist prepare the next Beta marketplace submission.

# Cloth NeXt 1.0.0 Stable

Cloth NeXt 1.0.0 is the first Stable release of the production Blender-to-PPF
Bake workflow. It promotes the tested 0.4 Beta line with final persistence,
diagnostics, Companion, and release-channel hardening.

## Production simulation workflow

- Bake multiple Cloth, Rod / Cable, and Soft Body objects in one shared PPF
  project.
- Use Follow Animation Pins in multi-object Bakes.
- Add every supported Cloth NeXt Force to Empties and animate its properties
  with Blender keyframes.
- Bake without a Collider. The required internal PPF STATIC sentinel remains
  remote and never appears as a Blender object or playback result.
- Stream verified solver frames into transactional PC2 caches while preserving
  the previous complete result until the replacement is valid.

## Failure safety and persistence

- Seventy-four stable `CNX-E…` codes distinguish validation, Companion, solver
  startup, upload, build, simulation, transfer, cache, and cleanup failures.
- Full failures persist locally in rotating `bake-errors.log`; per-run
  `failure.log` reports are replaced atomically.
- Failed Companion windows pulse red, show the concise error code, remain open
  for inspection, and close only through explicit user action.
- Authenticated status messages are bounded so a large traceback cannot break
  Companion IPC or mask the original error.
- RAM Auto Cancel defaults to 90%, uses two-sample debouncing, and reports
  actionable code `CNX-E166` when triggered.

## Interface

- CPU, RAM, and VRAM graphs focus on system load and show the RAM safety limit.
- Smooth, freely rotating Cloth NeXt icons drift through the Bake Companion.
- Physics roles and Forces use role-specific dropdown icons.

## Update channels

Channel visibility is cumulative:

- Stable releases are published byte-identically to Stable, Beta, and Dev.
- Beta releases are published to Beta and Dev.
- Dev snapshots remain available only through Dev.

Each repository exposes exactly one active Cloth NeXt candidate. Users who stay
on Beta or Dev therefore still receive a newer Stable release when it supersedes
their current test build.

## Compatibility

Cloth NeXt requires Blender 5.0 or newer on Windows x64. The PPF Contact Solver
remains a separate external dependency and is never bundled with the extension.
