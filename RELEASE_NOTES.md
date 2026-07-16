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
