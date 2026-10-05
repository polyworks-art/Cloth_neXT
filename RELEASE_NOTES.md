# Cloth NeXt 2.9.14

Object Attachments to deleted objects are removed automatically. Stale entries such as button.001 no longer block Bake with a source or target object no longer exists error. Cleanup runs after deletion, on file load and before Bake validation.

Attachments remain intact when an object still exists in Blender but is temporarily unlinked from the current scene. Incomplete attachment drafts are also retained.

Validated with attachment regression tests and a real Blender deletion/unlink smoke test. Update through Blender's native extension manager. The external solver is not modified or bundled.
