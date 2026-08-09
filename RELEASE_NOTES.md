# Cloth NeXt 2.2.22 Dev

Cloth NeXt 2.2.22 fixes live deformation feedback during Bake and makes Force
direction controls unambiguous in preparation for the 2.3.0 Beta milestone.

## Live Bake feedback

- Each growing PC2 cache is attached to its deformable before Blender advances
  the timeline, so completed frames visibly deform the mesh during the first
  Bake as well as subsequent runs.
- Final cache validation and authoritative playback setup remain unchanged.

## Force controls

- Gravity has an explicit X+, X-, Y+, Y-, Z+, or Z- world-axis selector and no
  longer follows the Force Empty rotation.
- Wind continues to use the Force Empty's local positive Z axis, so rotating the
  Empty aims Wind only.
- Air Density now defaults to the solver-recommended `0.01`. An inline warning
  explains why active Wind may appear ineffective at very low density.

This is Dev version `2.2.22`, is eligible only for the Dev channel, and prepares
the force and live-playback behavior for `2.3.0` Beta validation.
