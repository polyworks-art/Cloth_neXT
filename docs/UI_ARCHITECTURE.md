# Phase 2.8B UI architecture

The Pinning panel exposes Static and Follow Animation modes. Follow Animation reserves the run, captures one evaluated frame per bounded main-thread timer tick, reports dedicated capture activity, remains cancellable, and completes companion readiness only after the immutable target snapshot is valid.

The Bake companion separates overall progress from typed solver activity and never parses logs in Tk. Its rectangular mist preloads 32 seamless build-generated fog frames and swaps one full-Canvas image through a 45 ms timer. Broad state and activity adjust playback speed; reduced-motion and static fallback paths remain decorative. The packaged Windows DWM caption matches the `#303030` main-window background.

Production Bake is a Blender modal operator backed by an event timer. It
consumes editing events while active, keeps redraw and Cancel responsive, and
removes its timer once at terminal state. The companion reads the same shared
snapshots and enters Tk `-topmost` mode once when a Bake begins.

Production startup is now a readiness-gated sequence: scene/material/range and
solver validation, companion process and transport startup, `ENTER_BAKE_MODE`,
then a matching `BAKE_WINDOW_READY`. Only an acknowledgement for the current
job that confirms mapped visibility, topmost state, and transport readiness can
create the modal handler and acquire its job-owned lock. A seven-second Blender
timer bounds the wait without blocking the main thread. With automatic launch
disabled, Bake runs through Blender progress and Cancel without the global
modal lock.

All three presentation surfaces consume `BakeSnapshot` values from the one
thread-safe `BakeController`. The pure `cloth_next.bake` package imports no
Blender API and owns transitions, progress, formatting and the bounded JSON
schema.

Physics Properties is the primary interface. Cloth objects show Overview,
Solver, Material, Damping, Collisions, Cache and Advanced PPF. Colliders show
only Overview, Solver, Collisions, Cache and Advanced PPF. No N-panel is
registered.

Solver contains a scene-wide Solver Quality section: Time Step and Minimum
Newton Steps (Basic), then PCG Max Iterations and PCG Tolerance (Advanced).
They map directly to `dt`, `min-newton-steps`, `cg-max-iter`, and `cg-tol`;
there is no stored `substeps` property. Material contains object-local Pressure
for Cloth/SHELL only. Disabled Pressure encodes `pressure = 0.0`.

Explicit Dev snapshots may also show one collapsed, Cloth-only Developer Tools
subpanel under Cache. It is hidden by default and requires the existing
Developer Tools preference. Real Solver Test and UI Diagnostics share one
native alert-styled box there; they are not part of the production workflow.
Beta and stable packages have no Dev metadata, so this UI fails closed even if
an older preferences file has the checkbox enabled.

## Production Bake entry point

`CLOTHNEXT_PT_solver` shows solver readiness without executable paths, the
primary Bake/Rebake/Bake Again action, typed progress, Cancel, selected preset,
supported counts, the selected Bake range, and cache state. Unsupported scope disables Bake
with an explicit reason; installation details remain in Add-on Preferences.

Production Bake and Developer Real Solver Test call the same material-aware
`solver_test.start_run` application service. The main thread validates scope
and freezes Shell/Static settings before solver resolution, companion launch,
worker creation, sockets, or PPF startup. The worker receives only the
immutable `RunPlan`. The optional companion subscribes to the shared
`BakeController`; it is never a second simulation authority.

`CLOTHNEXT_PT_pinning` is a Cloth-only Physics subpanel with Enable Pinning,
the active Cloth object's native vertex-group selector, and a bounded pinned
vertex count. Controls lock during startup and active runs. Bake validation
freezes an immutable `StaticPinSnapshot` on Blender's main thread at Bake Start,
validates binary membership and source/evaluated topology, restores the user's
timeline frame, and only then may resolve PPF or open the companion. No Blender
RNA object crosses the worker boundary.

## Phase 3B material UI

Honest-controls policy: every visible, editable property maps to a real PPF
parameter. The former Quality, Physical (Stretch/Shear/Thickness), Pressure,
and Shape subpanels and the editable Cache range are removed until their
mappings are verified; the Cache panel shows the read-only development
slice notice instead.

Preset service: the bundled `cloth_next/materials/ppf_fabric_presets.toml`
is parsed and validated exactly once at import (registration) time into
immutable `MaterialPreset` values; the EnumProperty items are a static
tuple, so no `Panel.draw` ever reads the file. Selecting a preset applies
its mapped values through one guarded update callback (reentrancy-safe,
main-thread, undo-compatible); manually editing any preset-controlled value
switches the selection to Custom without resetting values. A malformed
bundle degrades the selector to Custom, shows the load error in the
Material panel, and applies nothing.

Blender-to-pure snapshot: `solver_test.build_run_plan` freezes all
PropertyGroups into immutable `ShellMaterialSettings` /
`StaticMaterialSettings` dataclasses on the main thread while building the
`RunPlan`. Validation failures surface in PREPARING with property, value,
accepted range, and remedy — before any worker thread or solver process
starts. The worker only ever sees pure values and never touches ``bpy``
(enforced by tests). The Advanced PPF panel and the "Inspect Encoded
Parameters" developer action show both artist names and exact wire
spellings from one shared formatting table.

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
