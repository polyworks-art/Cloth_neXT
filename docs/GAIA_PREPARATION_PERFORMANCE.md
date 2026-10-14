# GAIA Flow preparation performance — 2026-10-14

Implemented locally against 2.9.23; these changes are not in the published ZIP or the running installed add-on. Version and selected GAIA 0.23 are unchanged.

## Confirmed interruption

The installed 2.9.23 GAIA Flow window displayed reconstruction frame 177/250 after 1:55:37, with overall progress 1927/3000. The three-object progress was processing its second object. No native solver process was running. At the user's request, Cancel was clicked; the window subsequently confirmed `Water Flow cancelled`. Blender remained open. No Blender process was terminated or source scene saved.

## Corrections

- All enabled Cloth objects now share the scene Flow source, grid, coupling and `.gaia` destination. Influence and Velocity Scale remain individual. One field is prepared for the shared destination, rather than reconstructing the same fluid for each object.
- Trilinear reconstruction uses scalar channel accumulation and precomputed flat indices. All source particles, float64 accumulation and addition order are retained; the final arrays are bit-identical to the prior implementation.
- Verified preparations can be reused by subsequent jobs in the same Blender session. Every source file and the `.gaia` file must retain its size/time stamps, and field metadata must match. Changed source/cache/grid data trigger validation again. Runtime frame checksums remain active. Restarting Blender clears the session receipts.
- Temporary preparation frame changes mask FLIP diagnostic GPU previews and restore their exact flags immediately afterwards, including on exceptions. Physical FLIP surface/particle meshes, animation samples and solver inputs remain enabled.

The 1-GiB payload and 512-MiB reconstruction limits are unchanged. The changes do not add host forces or change the solver.

An additional local test launch of the workspace's generated Companion executable left an `Unhandled exception in script` dialog: `LocalSocketClient` timed out during `socket.create_connection`, before entering the application's exception handler. This was a separate executable from the installed GAIA Flow window. The dialog was closed. Companion startup now logs connection timeout/refusal and exits with code 2, allowing Blender's existing startup diagnostics to handle it. Targeted startup/onboarding regression: **9 passed**, including timeout and refused-connection cases. The existing frozen executable has not been rebuilt with this correction.

## Actual measurements

Blender 5.2.2 LTS, existing `SW04_NEON.blend`, RTX 4070 SUPER, Ryzen 9 7900X. Evidence is in `validation/gaia026/preparation-performance/`.

| Test | Previous path | Optimized path | Verification |
| --- | ---: | ---: | --- |
| Reconstruction frame 1, 8,784,881 particles | 7.107 s | 1.267 s | Velocity and occupancy exactly equal |
| Reconstruction frame 125, 8,784,791 particles | 6.638 s | 1.305 s | Velocity and occupancy exactly equal |
| Reconstruction frame 250, 8,783,879 particles | 7.028 s | 1.362 s | Velocity and occupancy exactly equal |
| Three-frame scene evaluation, three Cloth and two colliders | 5.084 s | 2.648 s | Evaluated vertices and world matrices exactly equal |
| Prepare shared field, three existing Cloth objects, three real FLIP frames | — | 12.856 s | One worker and three captures total |
| Subsequent Bake preparation in the same session, changed Influence | — | 0.910 s | Zero workers/captures; `.gaia` bytes unchanged |

Source files were unchanged during reconstruction tests and the source `.blend` checksum was unchanged during scene profiling. The excluded `SW_04_body_lod0_mesh.001` was not included as a collider. The warm-reuse test restored the timeline. Blender RNA/save-load sharing smoke testing passed again after the performance changes.

Full source regression: **2,262 passed, 7 skipped, 17 deselected** in 157.49 seconds. The Git-dependent generated-executable tracking check was run separately in the actual repository: **1 passed**. The full source run uses a clean HEAD archive overlaid with all working source changes, because the live workspace retains generated packaging artifacts used by previous validation jobs. An initial run in that artifact-containing workspace passed 2,261 cases and failed the source-root packaging check; the clean-source run includes that check and passes it. Integration/built-artifact cases are not represented by these counts.

Reproduction tools: `tools/benchmark_gaia_reconstruction.py` (existing `.gaia`, real frames), `tools/blender_profile_preparation.py` (`--blend`, `--output`, `--frames`), and `tools/blender_benchmark_water_reuse.py` (`--blend`, fresh `--output`). Blender tools are launched with `--background --factory-startup --enable-autoexec --python <tool> -- <arguments>`; no preferences or source scene are saved.

Scene profiling uses headless Blender with GPU drawing itself stubbed, while baseline debug-file loading remains enabled. It measures preparation CPU work, not interactive GPU drawing. The optimized scene pass follows the baseline with warm filesystem caches; it is a representative measurement, not a guaranteed twofold improvement for every scene.

## Limits and untested cases

The first preparation still reads and validates the complete source cache. A new Blender session cannot use a prior session's in-memory receipt. A complete 250-frame NEON bake with these optimizations has not yet been run; the reconstruction speedup is not a measured speedup for the entire bake. The representative three-frame reuse test performs preparation, not a native solver run. Previously executed native GAIA 0.26 tests are documented separately and predate these performance changes.
