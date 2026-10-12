# Cloth NeXt 2.9.17

Fix missing intersection highlights after a Bake fails during scene validation. The official solver frontend can report many triangle pairs in one diagnostic record. Cloth NeXt now unpacks those pairs before mapping them to source objects and faces, so the viewport overlay can show the offending geometry for repair.

Cloth/Collider intersections retain both reported sides. Solver totals remain visible even when only some detailed previews are available. Individual modern diagnostic records remain supported.

The fix was checked against the actual failed scene's 39 reported pairs: all 39 mapped successfully and produced overlay geometry. Automated tests cover grouped and individual records, incomplete preview counts, the failed-Bake sidecar handoff and real Blender handler lifecycle. These checks are mandatory in the private release pipeline.

Update through Blender's native extension manager and restart Blender to load the corrected diagnostic mapper. The external solver is not modified or bundled.
