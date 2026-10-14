# Cloth NeXt 2.9.25

Solver selection failures now show their reason in Preferences. An unhealthy installation, missing registration or registry write failure no longer leaves the user with a silently rejected selection. The previously selected solver is preserved on failure, and successful selection clears the old error.

In the reported NEON case, GAIA 0.26 was blocked by an old local validation marker. A fresh real health check passed, the marker was repaired locally, and selecting GAIA 0.26 in Blender persisted correctly. This release does not bypass health checks or automatically change solver selection.

The shared GAIA Flow and preparation improvements from 2.9.24 are retained. The external solver is not bundled.
