# Cloth NeXt 2.3.5 Dev

Cloth NeXt 2.3.5 makes the Bake Companion animation smoother and removes the
periodic Blender stutter that could continue after a bake had finished. This is
a Dev release for validation before the next Beta.

## Smoother Bake Companion

- Every particle icon now has four subpixel phases on each axis. Slow movement
  therefore advances in quarter-pixel visual steps instead of visibly jumping
  between integer Canvas positions.
- The particle artwork is 5% larger without changing its motion bounds or
  exceeding the established opacity limit.

## No recurring work after a bake

- The HUD redraw timer follows the controller's active state. A terminal bake
  transition receives one final redraw, then stops invalidating 3D views.
- GPU and system telemetry pauses while no bake is active and resumes for the
  next bake without recreating the service thread.
- The viewport-color timer only requests a redraw when it actually changes a
  viewport to Object Color mode.

## Included validation

- The normal repository suite passes 1,655 tests, with 10 configured external
  integration cases skipped honestly and 3 built-artifact cases reserved for
  the publication build.
- Focused regression tests cover subpixel motion and generated assets, terminal
  HUD behavior, telemetry pause/resume, and no-op viewport-color refreshes.

The external PPF Contact Solver is unchanged, remains a separate installation,
and is not bundled with Cloth NeXt.
