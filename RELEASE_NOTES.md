# Cloth NeXt 2.9.19

Improve Wind performance during viewport navigation, timeline preview and Bake preparation. Unchanged redraws reuse vector buffers and GPU batches instead of repeating geometry and airflow queries.

Static obstacles reuse cached geometry across frames. Geometry edits and animation update the affected surfaces, while bounded query caches and spatial tests avoid unnecessary work. Collider surfaces that already match their simulation input boundary no longer create temporary objects or trigger repeated whole-scene dependency updates. Preview meshes with uniform transforms use Blender's native BVH construction.

The fix was measured in an open animated character scene with approximately 169,000 triangles and Grooms disabled. Timeline steps fell from 663 ms to 48 ms in Collider mode and from 5.39 s to 87 ms in Scene mode. Three-frame Wind preparation fell from 8.22 s to 0.41 s in Scene mode, with byte-identical exported payloads. Dense animated scenes can still exceed realtime frame budgets; these measurements cover Wind preparation rather than native simulation time.

Validation includes 2,174 passing source tests, real Blender cache invalidation, transformed obstacle queries, timeline and cancellation checks, and GPU overlay captures.

Update through Blender's native extension manager and restart Blender. The external solver is not modified or bundled.
