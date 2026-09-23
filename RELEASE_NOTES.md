# Cloth NeXt 2.7.8

The expanded Bake Companion Solver details now use the active solver's verified runtime identity. Gaia reports its actual CUDA, CPU, or ROCm backend and device when available; protocol and schema compatibility metadata are no longer presented as the backend.

Live values are read from the real solver status summary for frame and step timing, matrix assembly, linear solve, line search, step advancement, contact memory, and stretch. Missing values create no rows, the expanded area follows its visible content, and collapsing restores the exact existing Bake Companion size. Lumen continues to degrade cleanly.

The add-on preferences now label the solver section GAIA Engine and show the supplied transparent GAIA leaf mark.

Published to the configured private repository. The external PPF Contact Solver is not bundled.
