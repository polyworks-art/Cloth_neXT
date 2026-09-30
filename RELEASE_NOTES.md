# Cloth NeXt 2.9.1

Cloth NeXt 2.9.1 turns Object Attachments into a dedicated artist-facing viewport workflow. Click Add Attachment, select source vertices directly on a wireframe overlay, choose another Cloth or Soft Body object, and select its target vertices. Live GPU connection lines preview the result before anything is committed.

Mappings are deterministic, based on world-space proximity, and remain useful when source and target counts differ. The source owns the editable Attachment and solver-backed Strength; the target shows only a read-only reference. Rename-safe object pointers, topology fingerprints, active-bake locks, and complete modal cleanup keep the relationship predictable through normal production changes.

Existing vertex-group Attachments remain compatible and continue to bake through the established multi-object solver path.

The external PPF Contact Solver is not bundled.
