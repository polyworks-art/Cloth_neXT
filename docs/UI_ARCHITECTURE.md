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

Launch is explicit. Blender validates and launches only
`bin/cloth-next-bake.exe` using `companion_manifest.json`; source mode requires
the explicit developer override. Blender tracks the exact child. Unregister
stops the preview, authenticates companion shutdown, closes IPC, then terminates
only that exact child if graceful exit fails. Panel, HUD and companion always
observe one controller.

## Phase 3A.1 live presentation

The pure `telemetry` package owns one stoppable worker. At a throttled one-second
default interval it queries `nvidia-smi` with a bounded explicit argument list
and Windows CPU/RAM APIs, then replaces an immutable cached snapshot. HUD draw
callbacks only read that snapshot and the shared `BakeSnapshot`; they never
launch processes, query hardware, access files, or mutate Blender data.

Responsive compact and expanded layouts support four anchors and scaling, with
a local compact fallback for narrow viewports. Typed job kinds distinguish UI
previews, solver tests, and future bakes. An explicit real run transitions to
PREPARING, optionally launches or reuses the authenticated companion, and then
validates the scene. Launch failure is a visible warning, not a solve failure.
