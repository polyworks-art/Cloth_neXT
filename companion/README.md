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
extension package. No solver or PPF files are included. This binary is not
distributed; future distribution would require a separate policy decision.
