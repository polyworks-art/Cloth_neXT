# Cloth NeXt 2.9.24

GAIA Flow setup is shared by all enabled Cloth objects in a scene. Enabling or changing Flow updates the common fluid source, coupling, grid and .gaia cache. Influence and Velocity Scale stay individual.

Fluid preparation reconstructs the shared field once. Subsequent Bakes in the same Blender session reuse verified preparations when source/cache files and field settings are unchanged. A new session still performs source validation.

Three real NEON frames with about 8.8 million particles each were reconstructed in 1.27-1.36 seconds versus 6.64-7.11 seconds for the previous math, with bit-identical velocity and occupancy. This measures reconstruction, not the entire Bake; an optimized 250-frame Bake remains untested.

General preparation skips diagnostic FLIP GPU previews during temporary frame changes and restores their settings immediately. Physical fluid meshes and animation remain enabled. A three-object, three-frame Prepare took 12.86 seconds; subsequent Bake preparation took 0.91 seconds without another capture or reconstruction.

A failed local Bake-window connection now exits cleanly instead of leaving an unhandled-exception dialog. Existing memory and 1-GiB payload limits, the .gaia format, selected solver and experimental GAIA 0.26 download option are retained. The external solver is not bundled.
