# Cloth NeXt 2.2.35

Cloth NeXt 2.2.35 hardens recovery ownership, keeps validation reliable across
Blender file loads, and prevents stale cache reuse across solver releases.

## Recovery and cache safety

- Recovery cleanup only removes checkpoint paths derived from Cloth NeXt's
  owned recovery root; persisted paths cannot authorize arbitrary deletion.
- Confirmed checkpoints can be abandoned cleanly.
- Scene cache identity includes the exact resolved solver installation and
  release, preventing reuse after a solver switch.

## Blender lifecycle and Companion

- Validation handlers remain persistent when opening or replacing `.blend`
  files, with teardown checks guarding against duplicate registrations.
- Missing worker status retains specific installation and quarantine guidance.
- Companion transport polling is non-blocking when no status message is ready,
  keeping the animation loop responsive.

## Validation

- New real-Blender harnesses cover file-load lifecycle and solver identity cache
  reuse/invalidation in addition to expanded unit and smoke coverage.
- Full Python suite: 1,379 passed, 9 skipped, 3 deselected.
- All 9 external solver integration tests passed separately with the official
  Lumen solver. The external PPF Contact Solver is not bundled.
