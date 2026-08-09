# Cloth NeXt 2.2.20 Dev

Cloth NeXt 2.2.20 improves the everyday Blender workflow, viewport feedback,
contact tuning, live Bake playback, and the Bake Companion details view.

## Blender workflow and viewport

- Cloth NeXt roles are color-coded in the viewport: Cloth is blue, Colliders
  red, Rods orange, Soft Bodies green, Rigid Bodies purple, and Forces yellow.
- Force objects expose all available controls together instead of requiring a
  separate Force Type to be added first.
- Cloth NeXt UI actions consistently use the bundled Cloth NeXt icon set.

## Simulation and live Bake feedback

- Artist-facing friction values keep the same UI while mapping to a gentler
  solver range; a UI value of 0.5 now sends 0.25 to the solver.
- Completed Bake frames advance Blender's timeline automatically to the newest
  available frame, with the complete baked range exposed after finalization.
- Bake recovery avoids appending duplicate PC2 samples after interruptions.

## Bake Companion

- The collapsible Details area now uses a compact Houdini-inspired grouped
  layout for run and solver statistics.
- The old performance graph is replaced by Frame, Progress, Elapsed, Solver,
  Contacts, Newton, Linear Iterations, and Activity values while ETA remains.

This is Dev version `2.2.20` and is eligible only for the Dev channel.
