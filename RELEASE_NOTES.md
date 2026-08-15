# Cloth NeXt 2.2.34

Cloth NeXt 2.2.34 improves Bake-window visibility, avoids unnecessary animated
Collider recapture, and reports solver build percentages correctly.

## Bake window

- On Windows, the active Bake window now uses native passive topmost ordering.
- It remains visible above Blender without repeatedly taking keyboard focus,
  and returns to normal window behavior after the Bake ends.

## Animated Collider reuse

- Completed animated Collider captures are stored in the existing verified
  export cache and reused when all solver-relevant capture inputs are unchanged.
- Geometry, animation, transform animation, safe modifier dependencies, capture
  mode/rate, frame range, and FPS participate in the cache identity.
- Missing, partial, corrupt, or uncertain cache state triggers full recapture.

## Progress semantics

- PPF's normalized BUILDING progress is now shown as a percentage such as
  `43%`, not as a simulation frame.
- `Frame X / Y` and frame ETA remain reserved for actual simulation frames.

## Validation

- Regression coverage includes native topmost semantics, Collider cache hits
  and invalidation, artifact integrity, BUILDING/SIMULATING separation, and
  frame-estimator isolation.
- Full Python suite: 1,372 passed, 9 skipped, 3 deselected.
- The external PPF Contact Solver is not bundled.
