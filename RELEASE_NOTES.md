# Cloth NeXt 2.9.7

Cloth NeXt 2.9.7 introduces the first GAIA Water Flow implementation. Baked FLIP liquid particles and their velocity attributes are reconstructed into a time-dependent field and applied through official GAIA 0.23 force fields, sampled at current simulated cloth positions.

Select a Cloth object and open Physics > GAIA Water Flow. Enable the feature, select the baked FLIP Domain, set Influence, Velocity Scale and resolution, choose a `.gaia` container path, then prepare the water field. Optional viewport vectors show the reconstructed contribution. Use GAIA 0.23 and ensure the FLIP bake exports fluid-particle velocity attributes.

Derived fields and source fingerprints are stored in checksum-verified `.gaia` containers. Empty support and positions outside the field add no flow. Bake and Cloth FPS must match. Schedules have a 32 MiB uncompressed upload limit; reduce resolution or the frame range when necessary. The effect uses GAIA's existing scene air-density and air-friction model; independent water drag and production streaming are not included.

The unchanged official CUDA solver passed synthetic field checks and a test with actual FLIP data. The immersion fixture showed an additional mean 0.1627 m displacement along X over four simulated frames. The original source Plane was outside the water in those frames, so the fixture was translated before simulation without editing the source scene. Full original-scene Water Flow cancel/resume acceptance and interactive debug-vector appearance remain unverified.

Wetness, buoyancy, tearing and two-way fluid coupling are not part of this release. The external PPF/GAIA solver and FLIP Fluids are not modified or bundled. Update through Blender's native extension manager.
