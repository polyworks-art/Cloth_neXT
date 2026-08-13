# Cloth NeXt 2.2.32

Cloth NeXt 2.2.32 makes the Bake window appear promptly after clicking Bake,
including in complex scenes that require lengthy preparation.

## Bake startup

- The Companion process now launches before scene validation, topology
  hashing, evaluated geometry capture, and run-plan construction.
- Expensive Blender-side preparation continues after the window is visible,
  so the Bake action no longer appears unresponsive while that work runs.
- Animated Pin and Collider capture retains the existing readiness gate and
  does not begin until the Companion transport is ready.

## Validation

- Regression coverage verifies that Companion launch precedes scene
  validation and geometry preparation.
- Full Python suite: 1,358 passed, 9 skipped, 3 deselected.
- The external PPF Contact Solver is not bundled.
