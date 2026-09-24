# Cloth NeXt 2.7.9

Cloth NeXt now provides one visible modifier-stack boundary for Cloth, Soft Body, Rigid Body, and mesh Colliders. Topology-changing modifiers before the boundary contribute to solver or collision geometry; modifiers after it remain Blender-only presentation effects.

For simulated meshes, the same owned Cloth NeXt modifier becomes PC2 playback after a successful bake. For Colliders it remains an evaluation boundary only, with no PC2 requirement. Boundary extraction now uses an isolated temporary object and leaves the user's modifier state, mesh, selection, active object, and mode untouched.

Topology compatibility is checked at the start, middle, and end of the bake range, and legacy Cloth-NeXt-owned cache modifiers remain adoptable. Topology-changing input modifiers still cannot be combined with Pinning, Sewing, or Friction Vertex Groups because those controls use base-mesh indices. Animated topology changes occurring only between the sampled validation frames may not be detected.

Published to the configured private repository. The external PPF Contact Solver is not bundled.
