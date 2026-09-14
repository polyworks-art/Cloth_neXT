# Cloth NeXt 2.2.0 Beta

Cloth NeXt 2.2.0 brings the latest development line to the Beta channel with a
major focus on animated-character workflows, preparation speed, recovery, pin
control, and a cleaner artist-facing interface.

## Animated character simulation

- Corrected the Protocol 0.13 / Schema 2 animation timebase so dense animated
  Collider samples improve motion fidelity without becoming additional logical
  solver frames or stretching the simulation duration.
- Added verified support for the current Protocol 0.13 solver alongside the
  existing Protocol 0.11 release, including side-by-side managed and external
  installations with an explicit active-solver selector.
- Added animation-aware Character Collision Cage proxies for conservative
  per-bone character collision setups.
- Improved animated Collider and Pin preparation through reusable evaluated
  data, cache identities, and reduced repeated Blender dependency-graph work.

## Pins and materials

- Added configurable Hard and Soft Pin constraints, including yielding pin
  behavior and animated targets synchronized with Collider sampling.
- Expanded the bundled material library to 75 categorized presets, combining
  scientific references with practical product-oriented starting points.

## Bake, recovery, and diagnostics

- Recovery data is preserved after unexpected solver exits when a valid
  authenticated checkpoint remains available.
- Recovery identity now includes the exact selected solver installation and
  official release, preventing incompatible checkpoint reuse.
- Added an estimated fill indicator for the solver's current frame alongside
  the existing progress and performance statistics.
- Hardened solver ownership, shutdown, worker diagnostics, cache identity, and
  scene lifecycle handling across Blender reload, cancellation, and failure
  paths.

## Interface and workflow

- Reworked Physics Properties into role-specific workflows for Cloth, Cable /
  Rope, Soft Body, Rigid Body, Collider, and Force objects.
- Grouped controls into clearer Setup, Simulation, Material, Shape, Collision,
  and Advanced sections with role-appropriate icons and descriptions.
- Improved Collider proxy, update, solver-selection, validation, and recovery
  feedback while keeping detailed Bake telemetry in the dedicated Bake window.

## Distribution

- Release tag: `2.2.0` — no leading `v`.
- Channel: **Beta**; the verified package is published to Beta and Dev only.
- The PPF Contact Solver remains separate and is downloaded only from its
  manifest-pinned official upstream release after explicit confirmation.
