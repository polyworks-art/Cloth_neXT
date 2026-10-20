# Cloth NeXt 2.9.34

Hydrodynamic Pressure now works in any scene and for any number of frames. Enable it on the cloth's Fluid Interaction panel with a GAIA CUDA solver selected; the add-on checks the selected installation itself (hash list and native worker probe) and never switches solvers. The earlier owner evaluation package, frozen NEON scene and 130-159 frame limit are no longer needed.

FLIP surfaces are read straight from the FLIP cache. Small surface holes no longer stop the Bake. Preparation runs in parallel, needs about 15 MB per frame and is removed after each Bake. Pressure samples one FLIP frame beyond the Bake end, so bake FLIP one frame longer than the cloth (for example FLIP to 250, cloth to 249).

Development limitations: pressure is one-way (water moves cloth), uses water density 1000 kg/m³ with Cn 1 and Ct 0, requires Blender Unit Scale 1, an unrotated FLIP domain and a shared FLIP cache for all wet cloth. Includes everything from 2.9.33.
