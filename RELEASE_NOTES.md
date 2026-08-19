# Cloth NeXt 2.2.42

Cloth NeXt 2.2.42 makes Re-Bake playback transactional and independent from
Windows successfully deleting or unlocking the previous PC2 cache.

## Transactional playback generations

- Every Bake writes a separate generation; the active cache is never
  overwritten in place.
- The previous successful generation remains authoritative throughout export,
  simulation, PC2 writing, and validation.
- The existing Cloth NeXt Mesh Cache modifier is retargeted only after the new
  generation is complete and validated.
- Commit failures restore the previous modifier path, visibility, stack
  position, ownership metadata, and Bake state.
- Multi-object Bakes preflight every cache and roll back coherently rather than
  leaving a silent mixture of generations.

## Safe Windows cleanup

- A locked obsolete PC2 no longer turns a successful Re-Bake into an error.
- Obsolete locked generations remain bounded deferred garbage and are retried
  only at later safe cleanup points.
- The cache currently referenced by active Cloth NeXt playback is never
  garbage-collected.
- Clear removes Blender playback state even when Windows keeps the obsolete
  file locked.
- Artist-created Mesh Cache modifiers and files are never retargeted or
  removed.

## Additional fixes

- Auto Fix ignores unrelated scene objects without persistent Cloth NeXt
  identity.
- The Bake window identifies the object actually affected by validation
  diagnostics.
- Animated-collider cleanup uses the valid Blender dependency-graph context.

## Verification

- Full Python suite: 1,419 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS real Windows file-lock lifecycle passed, including Unicode
  paths, modifier reuse, generation swap, artist-cache preservation, and Clear.
- The external PPF Contact Solver is not bundled or modified.
