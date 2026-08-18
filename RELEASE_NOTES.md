# Cloth NeXt 2.2.40

Cloth NeXt 2.2.40 exposes the intersection diagnostics and Auto Fix action in
the production `Simulation` panel that is actually visible for Cloth objects.

## Visible production controls

- After a solver-confirmed intersection failure, the `Simulation` panel shows
  the mapped faces, navigation, selection, solver-input preview, Clear, and
  **Auto Fix Intersections** when the retained mapping is safe.
- The controls are rendered by the same shared diagnostics component used by
  Developer Tools.

## Regression protection

- A production UI test now polls and draws `CLOTHNEXT_PT_simulation` for a real
  Cloth role and verifies that `clothnext.intersection_auto_fix` is present.
- The full Python suite passes with 1,395 tests.
- The external PPF Contact Solver is not bundled or modified.
