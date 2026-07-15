# Cloth NeXt Bake companion (source preview)

The compact window uses a 76×72 Houdini-inspired icon particle field at roughly 22 FPS. Approved add-on icons drift in independent random directions, follow smooth sine-based path noise, and wrap around the Canvas edges. Build-time Pillow derivatives keep the runtime lightweight; reduced motion freezes the field, and an asset failure leaves the background empty without affecting readiness. The upper bar shows overall progress while the lower bar consumes the dedicated typed solver-activity channel with a short debounce. `IconParticleField` owns and cancels its single `after()` timer on close.

This optional status client visualizes the same immutable real-bake snapshots as
the Blender panels and HUD. It does not run PPF itself; Blender owns the solver
session and sends status to the companion.

From the repository root, run `python -m companion.app` for the disconnected
demo view. Blender launches source mode explicitly with a random authenticated
`127.0.0.1` endpoint and publishes the active preview snapshots. Cancel sends a
request to Blender's shared controller; it never kills a process or touches files.

For a local development EXE, install `companion/requirements-build.txt`, then run
`python companion/build_companion.py`. Output is
`companion/dist/Cloth NeXt Bake.exe`, which is ignored and excluded from the
extension package for development. Release CI independently rebuilds it and stages
the validated result as the sole allowed executable inside the Windows extension.
No solver or PPF files are included.

The executable, taskbar, title bar, and Tk window use the approved `cloth_next`
project identity icon. The approved croissant remains distinct and appears only
as the Bake/progress symbol inside the window. Deterministic PNG/ICO derivatives
are produced by `python companion/build_assets.py` during the development build;
normal startup performs no conversion.
