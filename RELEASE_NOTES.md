# Cloth NeXt 2.8.4

Cloth NeXt 2.8.4 fixes simulation-boundary playback detection. An empty or disabled Cloth NeXt modifier no longer switches off Armature or Corrective Smooth modifiers placed before it.

When no active PC2 playback exists, Cloth NeXt restores previously muted input deformers and skips the unnecessary dependency-graph refresh that could briefly freeze complex character scenes. Active baked playback continues to mute already-baked input deformation to prevent double transforms.

Published to the configured private repository. The external PPF Contact Solver is not bundled.
