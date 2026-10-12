# Cloth NeXt 2.9.18

Author local Wind and Turbulence on Cloth NeXt Force Emptys. Animate direction, strength, radius and falloff, then enable Show Wind Vectors to inspect airflow on the timeline before Bake. Arrows stay near selected geometry, including moving objects and collection instances, with empty gaps left clear.

Choose Obstacles Off, Cloth NeXt Colliders or Scene Geometry. Optional response diverts airflow near surfaces, weakens it behind obstacles and adds an animated swirling wake. Wake Strength and Wake Length control the effect. This is a bounded one-way airflow approximation rather than a CFD solve; active simulation targets do not shield themselves using previous Bake results.

Local fields require GAIA 0.23 and positive Air Density. Animated air-velocity grids preserve existing Water Flow base fields; legacy global Wind remains available. Combined field schedules are limited to 32 MiB before allocation. Validation includes 2,174 passing source tests, real Blender timeline and obstacle checks, GPU captures and native CUDA airflow comparisons.

Update through Blender's native extension manager and restart Blender. The external solver is not modified or bundled.
