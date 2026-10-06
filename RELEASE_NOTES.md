# Cloth NeXt 2.9.16

Object Attachments now support enabled Collider targets with Gaia 0.23, including static, transform-animated and deforming Colliders. Cloth and Soft Body anchors follow the evaluated surface using stable vertex indices and barycentric weights.

Binding, preview and Bake-start validation include armature and shape-key deformation. Normal changes to a deformed quad's display triangulation retain its binding; changes to vertex numbering or polygon connectivity require rebuilding the attachment. Apply topology-changing input modifiers before binding. Older solver versions show an explicit instruction to select Gaia 0.23 for Collider Attachments.

Shape keys are evaluated when the Cloth NeXt simulation boundary is first in the modifier stack, and animated Collider capture excludes downstream modifiers. Existing deformable-to-deformable Attachments remain supported. The external solver is neither modified nor bundled.
