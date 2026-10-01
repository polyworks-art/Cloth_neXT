# Cloth NeXt 2.9.9

Water Flow now evaluates FLIP cache transforms before converting particle positions to world coordinates. This corrects the offset found in the supplied test scene. Existing fields with outdated coordinate provenance must be prepared again. Physical velocity components and the single Blender-to-GAIA axis conversion are preserved.

GPU overlays now upload contiguous float32 coordinates, fixing the malformed scene-spanning lines. Viewport vectors have bounded lengths, with sample-position, direction and constant +X diagnostics. GAIA Water Flow is a separate card with an inverted supplied icon. The preparation window is titled GAIA Flow; normal Bake retains Cloth NeXt Bake.

GAIA 0.23 installation validation now accepts repeated upstream documentation while still checking the required implementation. Onboarding seen state is stored durably to prevent repeat splash screens when opening files.

Validation includes unit regressions, real Blender UI/preparation checks, numerical source-to-grid-to-GAIA comparisons, and unchanged official CUDA solver checks for constant +X and an actual FLIP field. Full original-scene cancel/resume acceptance remains unverified. Use GAIA 0.23 and baked FLIP particle velocities. Update through Blender's native extension manager, then prepare existing Water Flow fields again.
