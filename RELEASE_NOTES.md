# Cloth NeXt 2.2.48

Cloth NeXt 2.2.48 hardens VEYRA as a general-purpose cloth topology and
intersection repair system. Ambiguous geometry is now preserved instead of
being repaired from assumptions learned from one garment scene.

## Proven safe topology repair

- Safe Weld considers only vertices belonging to solver-diagnosed
  self-intersections and never performs an unrestricted Merge by Distance.
- A disconnected-island weld requires an exact, coherent boundary chain and
  geometrically continuous surface evidence. Merely belonging to the same
  object is no longer considered proof of an import seam.
- Intentional lining, stacked panels, decorative patches, pockets, folded
  cuffs, near duplicates, and unrelated coincidences remain protected when
  repair intent cannot be proven.
- Point attributes, vertex groups and pin weights, materials, UV/corner data,
  seam/sharp flags, Shape Keys, linked data, and shared mesh datablocks retain
  their existing fail-closed protection.

## Generalized region solving

- Contact regions with more than two topological sheets are no longer forced
  into a two-side solution solely because their contact graph is bipartite.
- Adaptive 1%, 2%, 4%, and 8% displacements are all evaluated against local
  crossing reduction, edge and area safety margins, and movement cost.
- Two-sheet assignments, patch expansion, candidate ordering, independent
  batching, cache identity, and authoritative rollback remain deterministic.
- Every installed repair must strictly reduce the fresh global Lumen contact
  count. Equal, increased, cancelled, or failed attempts restore exact state.

## Generalization verification

- A new synthetic corpus covers clean meshes, intended duplicate seams,
  intentional layers, near duplicates, two-sheet penetration, folded and
  multi-sheet cloth, multiple independent regions, density and scale changes,
  semantic discontinuities, and multiple Cloth NeXt objects.
- Translation, rotation, uniform scale, vertex/face order, object naming, and
  region-order metamorphic checks remain functionally equivalent.
- Three frozen adversarial holdouts passed on their first run without tuning.
- Across the generalized corpus, destructive false-positive repairs were zero.

## Real-scene regression

- Blender 5.2.0 LTS and Lumen reduced the updated production scene monotonically
  from `2077 -> 2072 -> 1682 -> 1469 -> 1132`.
- All 18 Top degenerates were repaired. Three structurally proven Shorts weld
  clusters were accepted, while 145 unproven coincident boundary clusters were
  intentionally protected.
- Three region iterations were accepted without rollback, five authoritative
  Lumen calls were made, no frame simulation started, and the same Veyra
  Companion process remained active through completion.
- The original `IntersectionTest.blend` remained byte-identical with SHA-256
  `8402CE65A13A4D375985FDB681745F7FEBB93AFBBA6665B68831AF38F0D122B3`.

## Verification

- Full Python suite: 1,543 passed, 9 skipped, 3 deselected.
- Targeted VEYRA, UI, rollback, holdout, and pipeline coverage: 187 passed.
- The external PPF Contact Solver is not bundled or modified.
