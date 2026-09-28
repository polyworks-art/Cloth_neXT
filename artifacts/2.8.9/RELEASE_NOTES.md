# Cloth NeXt 2.8.9

Cloth NeXt 2.8.9 introduces Object Attachments for Cloth and Soft Body simulations. Select source vertices in Edit Mode, choose another Cloth NeXt Cloth or Soft Body target, and create persistent barycentric surface attachments that bake through the normal multi-object solver path.

Attachments include a transform-aware viewport overlay and fail-safe topology validation. Renamed objects remain connected through stable identities, while deleted objects, changed roles, stale topology, or invalid indices produce an actionable Needs Rebuild result instead of reaching the solver. This release also fixes the transition so the startup splash image is not reused by Welcome or What's New.

The external PPF Contact Solver is not bundled.
