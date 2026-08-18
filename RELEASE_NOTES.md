# Cloth NeXt 2.2.39

Cloth NeXt 2.2.39 makes the intersection repair controls from 2.2.38 visible
in the normal production Solver panel.

## Production UI hotfix

- Solver-confirmed intersection diagnostics now appear directly below the
  failed Bake status in the standard Solver panel.
- Navigation, face selection, solver-input preview, Clear, and **Auto Fix
  Intersections** are available there when the retained mapping supports them.
- The Developer Tools view reuses the same shared diagnostics renderer.

## Auto Fix safety

- Auto Fix remains limited to fully mapped, same-object cloth
  self-intersections.
- Unsafe, stale, collider, rod, proxy, sentinel, linked, or shape-key cases are
  still rejected rather than guessed.
- The external PPF Contact Solver is not bundled or modified.

## Validation

- The full Python suite passes with 1,394 tests.
- Release-policy and package-structure checks pass.
