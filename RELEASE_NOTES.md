# Cloth NeXt 2.2.15 Dev

Cloth NeXt 2.2.15 introduces Newton Physics as a selectable backend for the
existing offline Bake workflow. Live Preview is not part of this release.

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

This is Dev version `2.2.15` and is eligible only for the Dev channel.
