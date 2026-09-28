# Object Attachments v1

Object Attachments are persistent scene relationships between two enabled
Cloth NeXt deformables. Version 1 deliberately supports only Cloth (PPF
`SHELL`) and Soft Body (PPF `SOLID`) in all four source/target directions.
Cable / Rope, Rigid Body, and Collider objects are not accepted.

In Edit Mode, **Create From Selection** projects each selected source vertex
onto the closest triangle of the chosen target. The relationship stores stable
object identities, source indices, target triangle indices, barycentric target
weights, authoring-space world points, stiffness, and topology fingerprints.
Renaming either object is safe. A deleted object, disabled participant, changed
role, or changed topology marks the relationship **Needs Rebuild** and prevents
it from being encoded.

## Solver representation

The implementation was verified against the official upstream
`ppf-contact-solver` tags `2026-07-26-22-53`, `2026-08-12-15-47`, and
`2026-09-21-21-32` (repository head inspected at
`344bcb906203db19f3ea59ccd011d5a297f90390`). All supported Cloth NeXt
protocol adapters use the same `cross_stitch` group representation:

- six indices per point: a degenerate source barycentric triangle followed by
  the target surface triangle;
- six weights: `(1, 0, 0)` for the source vertex followed by target
  barycentric weights;
- source and target world points converted through Cloth NeXt's central
  Blender-to-PPF coordinate transform;
- upstream's normal `stitch_stiffness` default of `1.0`.

The explicit world points are retained because upstream uses them to recover
surface anchors after `SOLID` tetrahedralization. Relationships are encoded in
the regular multi-deformable parameter payload; there is no sidecar file or
alternate Bake path.
