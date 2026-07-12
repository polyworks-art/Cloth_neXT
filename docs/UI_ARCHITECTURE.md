# Phase 2.8B UI architecture

All three presentation surfaces consume `BakeSnapshot` values from the one
thread-safe `BakeController`. The pure `cloth_next.bake` package imports no
Blender API and owns transitions, progress, formatting and the bounded JSON
schema.

Physics Properties is the primary interface. Cloth objects show Overview,
Solver, Quality, Physical Properties, Damping, Collisions, Pressure, Shape,
Cache and Advanced PPF. Colliders show only Overview, Solver, Collisions, Cache
and Advanced PPF. No N-panel is registered. Settings are configuration UI and
are not mapped to PPF yet.

The display-only Viewport HUD has one reload-safe `POST_PIXEL` draw handler. Its
callback reads a snapshot and draws; it starts no work. Explicit UI preview
operators use one Blender timer and clearly state that PPF was not run.

Custom previews use `bpy.utils.previews`, load packaged PNGs once, tolerate
individual missing assets, and unload on rollback/unregister. SVG sources remain
under `assets/`; `tools/build_icons.py` is the explicit conversion step.

The optional companion is separate from both Blender and solver process
ownership. Blender binds one client on `127.0.0.1` at an ephemeral port with a
cryptographically random in-memory token. Newline-framed JSON is limited to 64
KiB and fixed hello/status/shutdown/ready/cancel/close message types. Socket
threads only enqueue requests; a Blender timer applies cancellation to the
controller. Disconnect never affects preview state.

Launch is explicit. Blender tracks the exact child, prefers the ignored local
development EXE, and falls back to configured Python source mode. Unregister
stops the preview, authenticates companion shutdown, closes IPC, then terminates
only that exact child if graceful exit fails. Panel, HUD and companion always
observe one controller.
