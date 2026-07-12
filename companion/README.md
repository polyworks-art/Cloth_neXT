# Cloth NeXt Bake companion (source preview)

This optional source application visualizes the same immutable bake snapshots as
the Blender panels and HUD. It is a UI preview only; it does not run PPF.

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
