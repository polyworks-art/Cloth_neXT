# Cloth NeXt 2.8.0

Cloth NeXt 2.8.0 completes PC2 playback at the modifier boundary and makes repeat bakes substantially cheaper. The owned Mesh Cache modifier is explicitly configured for PC2 and is enabled automatically after a successful bake.

Bake-start simulation geometry is now captured once during validation and reused for export. Animated topology validation reuses that start sample and only evaluates the remaining bounded timeline samples.

Unchanged animated Collider captures are reused from the persistent export cache. Self-contained deterministic modifier stacks such as Subdivision, Solidify, and Bevel participate in the cache identity; geometry, animation, modifier settings, sampling, frame-range, and FPS changes still invalidate the capture safely.

Published to the configured private repository. The external PPF Contact Solver is not bundled.
