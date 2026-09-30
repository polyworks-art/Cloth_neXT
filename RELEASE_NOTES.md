# Cloth NeXt 2.9.5

Cloth NeXt 2.9.5 fixes Sewing intersections caused by unequal path subdivisions. Instead of rounding multiple source vertices onto one target vertex, seams now match normalized rest-arc positions along target edges using the solver's existing barycentric stitch representation. This avoids collapsing adjacent seam vertices and applies to existing saved Sewing paths when rebaking. The editor and persistent overlay show the interpolated connections.

Valid solver-reported errors are no longer mislabeled as a lost solver connection. Runtime intersections identify the affected Blender frame and preserve available contact diagnostics. Actual network failures remain distinct.

The reported Sewing scene completed the full bake range through frame 250 with collisions enabled and Strength 100. This verifies the mapping fix for that scene, not a guarantee against every possible geometry intersection. Blender export and regression tests cover unequal subdivisions and flipped paths.

The external PPF Contact Solver is not modified or bundled. Update through Blender's native extension manager and rebake existing caches to use the fix.
