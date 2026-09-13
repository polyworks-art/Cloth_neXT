# Cloth NeXt 2.4.7 Dev

Cloth NeXt 2.4.7 adds an optional New Look for the primary Simulation workflow.
Enable it in Add-on Preferences to show a compact floating bar near the bottom
of each compatible 3D Viewport. Leave it off to keep the existing Simulation
panel. The preference defaults to off and can be changed without restarting
Blender.

The bar uses Cloth NeXt's existing Set Cache Directory, Quality preset, Bake,
Cancel and Diagnostics paths. Its directory bubble reflects the configured
folder, and Bake availability and progress use the existing run state. The
resource monitor UI has been removed; memory-safety telemetry remains active.

This is a presentation update. Existing simulation settings and Bake behavior
are preserved. The external PPF solver is not part of this release.
