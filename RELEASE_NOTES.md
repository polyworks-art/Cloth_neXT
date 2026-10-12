# Cloth NeXt 2.9.22

Smart Attach binds each selected source vertex to the nearest target surface vertex in world space. In Object Mode, choose Smart Attach, select source vertices, press Enter and click the target object. Closed source loops are supported, and multiple sources may share a target vertex. Bindings remain fixed during animation. Manual Add Attachment retains its open-path source/target selection workflow.

Attachment overlays reuse anchor geometry and GPU batches while navigating an unchanged scene. Endpoint edits, frame changes, attachment changes, load and undo invalidate the cache. Collider previews reuse their evaluated mesh when no visible modifiers follow the simulation boundary, avoiding unnecessary temporary geometry evaluation.

Material Zones can add multiple distinct property overrides reliably. Add Property no longer reuses Blender's previous property key or remove action, including after deleting an override.

Validation covers nearest-vertex closed-loop binding, persistent Attachment data, a single Collider anchor evaluation over 100 unchanged redraws, cache invalidation and multiple zone overrides with add/remove/add in real Blender.

Update through Blender's native extension manager and restart Blender. The external solver is not modified or bundled.
