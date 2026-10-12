# Cloth NeXt 2.9.20

Fix a remaining UI slowdown with local Wind. The floating Bake button's status checks were capturing and hashing obstacle meshes during redraws and background refreshes, even when Show Wind Vectors was disabled. These UI paths now read obstacle properties without capturing mesh geometry.

Full obstacle mesh identity is evaluated during explicit validation and belongs to the geometry fingerprint. Changes to Scene-mode obstacles still mark simulation geometry dirty, so removing UI mesh work does not skip cache validation.

In an open animated character scene, the complete Bake status check dropped from 88.9 ms to 2.8 ms. This measures the status path, rather than total viewport or native simulation time.

Validation includes 2,175 passing source tests and real Blender checks for 100 UI fingerprints without mesh capture, obstacle edit detection, and single- and multi-target geometry validation. These Blender checks are required by the private release pipeline.

Update through Blender's native extension manager and restart Blender. The external solver is not modified or bundled.
