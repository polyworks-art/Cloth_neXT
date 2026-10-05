# Cloth NeXt 2.9.11

Material Zones let different faces of one cloth object use independent Stretch Resistance, Bend Resistance, Friction, Shape Damping and Fold Damping. Other material controls inherit the object material. Zone definitions and face ownership persist in saved Blender files.

Use the viewport brush to select visible faces, Shift to remove assignments, the wheel to change radius, Enter to commit and Escape to discard. Occluded faces remain protected, including in X-Ray view. Bake validates topology and ownership before exporting exact per-triangle parameters; shared vertices do not blend neighboring zone values.

Validated with regression tests, real Blender save/load and viewport selection, and official GAIA 0.23 native material tables, including adjacent triangles with Bend Resistance 10 and 100. Full server-driven simulation validation remains limited by an installed frontend Windows junction cache-directory error; native table integration passes. See docs/MATERIAL_ZONES_VALIDATION.md for details.

Update through Blender's native extension manager. Material Zones require a supported managed solver installation. The external solver is not modified or bundled.
