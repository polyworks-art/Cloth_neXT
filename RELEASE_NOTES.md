# Cloth NeXt 2.9.10

GAIA Water Flow now streams current and next source frames from prepared .gaia caches through official GAIA 0.23 held Force Field updates. Temporal interpolation and source-frame offsets are preserved without uploading the full animation or reconstructing FLIP particles during the cloth solve. The native solver continues in the same session; the existing whole-schedule limit is unchanged.

Auto uses at least 100 samples on the longest axis. Medium, High and Extreme use 100, 150 and 200 with isotropic physical spacing, explicit memory limits and occupied-region preview sampling. The supplied scene's 100 x 100 x 50 field completes all 250 source frames through the production PC2 lifecycle. Tested 150 and 200 two-frame windows also pass actual production upload, output and cleanup.

Water Flow integrates with existing cancellation and Recovery. After restarting Blender, Resume loads the authenticated checkpoint's corresponding field window; Start Fresh resets simulation state while retaining the prepared environmental cache. Authenticated Windows project cleanup now handles long nested paths correctly. Water-disabled simulations keep their existing bake path.

Validation: 2,115 regression tests passed before release preparation, plus real Blender cancel/restart/resume/Start Fresh tests, numerical whole-versus-stream controls, 10/250-frame memory comparisons and actual unchanged official CUDA solver runs. The long test uses the supplied water sequence with a representative small cloth, rather than the full original scene. GPU/device memory was not separately measured.

Requires official GAIA 0.23 and baked FLIP velocity attributes. Update through Blender's native extension manager and prepare existing coarse Water Flow caches again for the new spatial recipe. The external solver is not modified or bundled.
