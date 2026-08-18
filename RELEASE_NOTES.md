# Cloth NeXt 2.2.41

Cloth NeXt 2.2.41 repairs the complete validation-diagnostics path for solver
self intersections and Blender preflight degenerate faces.

## Reliable validation geometry

- Solver-reported intersection totals are preserved independently from the
  number of faces that can be mapped safely.
- The Simulation panel and viewport now state both counts, for example
  `18 detected · 14 mapped`, and show an explicit warning when mapping data is
  incomplete.
- Solver triangle indices are checked against the exact solver triangle
  geometry before they are attributed to Blender objects and source polygons.
- Degenerate faces are highlighted as dedicated one-triangle diagnostics,
  including point markers for fully collapsed triangles.

## Disposable overlay sessions

- Every validation attempt owns one immutable diagnostic result.
- Clear, a new Bake, file load, and add-on shutdown remove retained geometry,
  navigation state, solver-input preview state, and GPU draw handlers.
- Handler setup and removal are idempotent and safe across reloads.

## Verification

- Full Python suite: 1,411 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS registration smoke test passed.
- The external PPF Contact Solver is not bundled or modified.
