# Cloth NeXt 2.2.33

Cloth NeXt 2.2.33 adds Corrective Smooth support to the geometry sent into the
deformable solver while preserving constant-topology playback safety.

## Corrective Smooth solver input

- Enabled Armature and Corrective Smooth modifiers can contribute to the
  Bake-Start geometry exported to PPF.
- Corrective Smooth also works without an Armature.
- The Cloth NeXt Mesh Cache is placed after the final solver-input modifier,
  preventing Corrective Smooth from deforming the finished simulation twice.
- Subdivision, Solidify, Geometry Nodes, Remesh, and other topology-changing
  modifiers remain downstream. A supported input modifier placed after such a
  topology-changing barrier is rejected with guidance to fix the stack.
- Disabled modifier state and artist-owned modifiers are preserved.

## Validation

- Regression coverage includes export-boundary selection, Corrective Smooth
  without Armature, disabled modifiers, exception restoration, unsafe stack
  rejection, playback placement, topology, and pinning behavior.
- Full Python suite: 1,363 passed, 9 skipped, 3 deselected.
- The external PPF Contact Solver is not bundled.
