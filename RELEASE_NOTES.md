# Cloth NeXt 2.2.21 Dev

Cloth NeXt 2.2.21 completes a UI consistency pass in preparation for the 2.3.0
Beta milestone.

## Blender workflow and viewport

- Cloth NeXt roles are color-coded in the viewport: Cloth is blue, Colliders
  red, Rods orange, Soft Bodies green, Rigid Bodies purple, and Forces yellow.
- Force objects expose Gravity, Wind, Wind Variation, Air Density, Air
  Friction, and Vertex Air Damping together without a Force Type dropdown.
- Gravity and Wind have independent strengths and can act simultaneously.
- Wind and Wind Variation default to zero, avoiding unintended gusts on a new
  Force object.
- Every Object Type menu entry uses a distinct semantic icon and a
  role-specific hover explanation.
- Nested workflow panels use semantic icons instead of generic placeholders.

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

This is Dev version `2.2.21`, is eligible only for the Dev channel, and prepares
the UI and workflow baseline for `2.3.0` Beta validation.
