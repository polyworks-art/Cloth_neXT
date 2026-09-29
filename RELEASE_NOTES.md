# Cloth NeXt 2.9.0

Cloth NeXt 2.9.0 makes Object Attachments direct and predictable. Add an attachment in Object Mode, choose the target object, select Vertex Group 1 on the source and Vertex Group 2 on the target, then bind the relationship explicitly.

Bound vertex pairs remain stable during animation and at bake time. Group edits, renamed groups, deleted targets, role changes, and topology changes stop with an actionable rebuild message instead of silently changing the connection. A spatial search keeps binding responsive for large vertex groups.

Attachments support Cloth and Soft Body in every source and target combination, persist through save and reload, and bake through the normal multi-object solver path.

The external PPF Contact Solver is not bundled.
