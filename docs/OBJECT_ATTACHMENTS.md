# Object Attachments

Object Attachments are persistent scene relationships from an enabled Cloth
(PPF `SHELL`) or Soft Body (PPF `SOLID`) to another Cloth, Soft Body, or Collider.
Collider targets require **Gaia 0.23**. Cable / Rope and Rigid Body endpoints,
and Collider sources, are not accepted.

## Collider targets

The Target Object picker, vertex-group binding and viewport Attachment editor
accept enabled Collider meshes. Static, transform-animated and deforming
animated Colliders all use the existing native `cross_stitch` constraints.
The Collider remains a `STATIC` scene group, and its existing transform or
per-vertex animation drives the target surface. No replacement pin targets,
solver modifications or duplicate collision meshes are introduced.

Binding and viewport preview use the evaluated surface at the Cloth NeXt
simulation-stack boundary, including armature and shape-key deformation.
Bake validation freezes the barycentric anchors in the evaluated Bake-start
pose and restores the user's timeline frame/subframe. The triangle indices
and weights remain fixed while the target moves; they are not re-projected
to different vertices during animation.

The target's surface vertex numbering and triangle connectivity must remain
unchanged. Apply topology-changing input modifiers before binding; changing
topology after binding requires rebuilding the relationship. The target must
be included as that same Collider in the Bake. A collision proxy which
replaces the selected target is not automatically substituted for an anchor
on the original mesh; use the actual exported Collider surface.

Gaia 0.22 and older adapters reject Collider Attachments with an instruction
to select Gaia 0.23. Existing Cloth/Soft Body Attachments retain their earlier
solver compatibility.

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
