# Cloth NeXt 2.9.8

Water Flow preparation now uses the existing Bake Window with progress, cancellation and two familiar progress bars. Details is hidden during preparation. Source hashing and field reconstruction run in a worker; Blender captures one FLIP frame at a time on its main thread. Production Bake prepares the fields and continues using the same job.

GAIA Path accepts a folder or a new file name; the container is created with the .gaia extension. Leave the path empty to use the Cloth cache directory. Water Flow controls now follow the Cloth Physics layout, Flow Display has its own section, and the card uses the supplied monochrome GAIA icon.

Preparation restores the original scene frame and stops before file load or add-on shutdown. A real FLIP-scene smoke test verified preparation, cache reuse and cancellation without changing the source scene or cache. The measured maximum main-thread capture step for this short test was approximately 80 ms; larger scenes may take longer per frame.

Use GAIA 0.23 and baked FLIP fluid-particle velocities. The existing 32 MiB upload limit and scene drag model still apply. Independent water drag, production streaming, wetness, buoyancy, tearing and two-way coupling remain outside this release. Full original-scene simulation cancel/resume and interactive vector appearance remain unverified. Update through Blender's native extension manager.
