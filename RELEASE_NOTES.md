# Cloth NeXt 2.2.18 Dev

Cloth NeXt 2.2.18 closes the remaining duplicate-generation startup race that
could immediately abort Newton Bake with E110. Live Preview is not part of
this release.

## Process-wide E110 fix

- Bake ownership is reserved before Newton or PPF launches the Companion.
- The reservation is shared by every loaded Cloth NeXt module generation in
  the Blender process.
- A stale PPF callback cannot take over, release, or modify Newton's Bake job.
- Preparation failures release their own reservation so Rebake remains
  available.

## E110 startup race fix

- Old PPF animated-Pin and Collider preparation timers cannot publish into a
  newer Newton Bake job.
- Every PPF preparation callback verifies its owning job before opening the
  Bake window or changing the shared Bake controller.
- Orphaned active Bake states can be cancelled without leaving Rebake locked.

## Newton Bake startup hotfix

- Animated and deforming Collider sampling advances cooperatively instead of
  blocking Blender's UI before the Bake window opens.
- Scene preparation progress is visible in the normal Bake window.
- Newton uses Blender's scene gravity when no Cloth NeXt Gravity Force exists.
- Gravity-related diagnostics are no longer incorrectly reported as E108 when
  the scene contains no Force object.

## Newton Bake workflow

- Select **PPF** or **Newton** as the scene solver.
- Select Low, Medium, High, Extreme, or Custom quality.
- Start Bake through the usual action.
- The normal Bake window reports startup, simulation, frame progress, cache
  writing, and import.
- Completed PC2 caches are attached to their original Blender objects.

## Supported Newton object types

- Cloth, including static and Follow Animation pins.
- Soft Body, tetrahedralized through pinned pytetwild/fTetWild 0.3.0.
- Rigid Body using Newton's native body and mesh-shape representation.
- Static, animated, and deforming Colliders with stable topology.
- Mixed Cloth, Soft Body, Rigid Body, and Collider scenes in one VBD solve.

Pressure, Sewing, Rods, non-gravity Force objects, Soft Body pinning, Soft Body
rest-volume scaling, and Newton recovery checkpoints remain unavailable and
fail closed instead of being silently ignored.

## Runtime and distribution

Newton 1.4.0, Warp 1.15.0, and pytetwild 0.3.0 are installed only after explicit
confirmation into an isolated CPython 3.11 environment outside Blender. They
are not bundled in the extension archive. PPF remains external and unchanged.

This is Dev version `2.2.18` and is eligible only for the Dev channel.
