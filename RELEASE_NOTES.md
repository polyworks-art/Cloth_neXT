# Cloth NeXt 2.9.23

GAIA Water Flow now separates Fluid Source, Cloth Response and Fluid Cache. Enable FLIP velocity export beside the domain selector, prepare a .gaia cache, and start the Cloth Bake. Native controls explain coupling strength and velocity damping.

The official Windows GAIA 0.26 solver is available as an optional download in Manage. It is experimental and must be selected explicitly. Existing solver selection is retained; the solver remains external software and is never included in the add-on package.

Large FLIP caches are processed in blocks of 65,536 particles without dropping particles or increasing the existing memory and 1-GiB native payload limits. NEON completed 250 frames with its three existing cloth meshes, totaling 23,356 vertices; finite coordinates and Blender playback passed. This result does not establish support for arbitrary scene sizes. Convergence depends on geometry and quality settings, and the complete installed interactive artist workflow has not yet been validated. This Dev release is not a public GAIA 0.26 beta qualification.

Native contact diagnostics, failed Re-Bake cache preservation and Windows long-path handling have also been improved. Before the final UI and download changes, regression recorded 2,232 passing tests and 13 configured external solver tests. Release checks are repeated for the published source revision.
