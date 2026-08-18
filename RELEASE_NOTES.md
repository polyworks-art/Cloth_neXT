# Cloth NeXt 2.2.38

Cloth NeXt 2.2.38 adds a conservative repair action for small initial cloth
self-intersections and makes the viewport diagnostics lifecycle reliable.

## Auto Fix Intersections

- Fully mapped, solver-confirmed self-intersections can be gently separated
  without remeshing, deleting faces, or changing topology.
- Both surfaces share the correction, repeated contributions to a vertex are
  averaged, and movement is clamped using local triangle scale.
- Blender Undo is supported, and the normal Bake workflow reruns afterward so
  the PPF solver remains the authoritative validation source.

## Safety

- Auto Fix is limited to same-object cloth self-intersections in this release.
- Stale diagnostics, changed topology or transforms, shape keys, linked meshes,
  colliders, rods, generated proxies, sentinels, and incomplete mappings are
  rejected instead of guessed.

## Intersection display reliability

- Clear and add-on shutdown now remove retained geometry and GPU handlers.
- Invalid triangle payloads no longer create count-only highlights.
- The UI explicitly reports detected and mapped counts when they differ.

## Validation

- The full Python suite passes with 1,393 tests; external solver integration
  prerequisites remain skipped when not configured.
- The external PPF Contact Solver is not bundled or modified.
