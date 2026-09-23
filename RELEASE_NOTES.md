# Cloth NeXt 2.7.7

The expanded Bake Companion details now focus on solver runtime information without changing the normal compact Bake view. The duplicate Run section has been removed, while the compact frame progress, Contacts, Newton Steps, and Linear Iterations presentation remains unchanged.

Solver details identify the active runtime and backend, such as Gaia CUDA, and show the device when the solver reports it. Optional live telemetry includes frame and step timing, matrix assembly, linear or PCG solve, line search, step advancement, contact memory, and stretch. Rows are omitted when the active solver does not report a value, so Lumen remains clean and compatible.

Published to the configured private repository. The external PPF Contact Solver is not bundled.
