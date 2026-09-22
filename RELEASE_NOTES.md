# Cloth NeXt 2.7.6

Gaia 0.22 is now the preferred external solver for new installations. Lumen 0.18 remains available, and existing verified Velune installations remain usable. Cloth NeXt handles their solver generations through explicit compatibility adapters.

Preferences now offers AUTO, CUDA, ROCm, and CPU backend choices. Explicit choices report an error when the selected solver does not support them. AUTO prefers a compatible GPU backend and can fall back to CPU. ROCm is offered with a warning because AMD support has only been checked with a lightweight five-frame bake on an APU; broad AMD verification remains open.

The backend certification covered Gaia CUDA and CPU, Lumen CUDA, an AMD APU ROCm smoke bake, and consecutive backend and solver switches in one Blender session. See `docs/SOLVER_BACKEND_CERTIFICATION.md` for results and limitations.

Published to the configured private Dev repository. The external PPF Contact Solver is not bundled.
