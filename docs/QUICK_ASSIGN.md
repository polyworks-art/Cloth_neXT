# Floating Quick Assign

Quick Assign floats centered above the existing New Look toolbar. The toolbar's
logo, cache-directory, quality, bake/cancel, diagnostic and F6 controls retain
their existing behavior. This checkout had no previous Quick Assign click action;
a short click on the new control makes no scene change.

## Implementation

- `cloth_next/quick_assign.py`: Blender-independent layout, sector hit testing,
  deterministic role order and gesture state.
- `cloth_next/blender/quick_assign.py`: context validation, assignment dispatch,
  gesture lifetime and drawing through the existing toolbar helpers.
- `cloth_next/blender/floating_simulation.py`: one additional button gizmo and
  draw call, plus cleanup hooks. No extra draw handler or pie-menu framework.
- `tests/test_quick_assign.py`: geometry, state, dispatch and cleanup regressions.
- `tests/test_floating_simulation.py`: F6 fully hides the detached button.
- `tools/blender_quick_assign_smoke.py`: real Blender registration, dispatch and
  optional live viewport event checks.

The existing `clothnext.add_physics` and `clothnext.set_object_type` operators
remain authoritative. A small undoable wrapper invokes them with nested undo
disabled, so enabling physics and choosing the role requires one Undo. No solver
or simulation setup logic is duplicated. Invalid object/role pairs are rejected
before either operator is invoked.

| Label | Stored role | Compatible object |
| --- | --- | --- |
| Cloth | `CLOTH` | Mesh |
| Cable / Rope | `ROD` | Curve |
| RBD | `RIGID_BODY` | Mesh |
| Soft Body | `SOFT_BODY` | Mesh |
| Collider | `COLLIDER` | Mesh |

**Selection semantics:** existing role assignment affects the active object only.
Quick Assign preserves this, including when several objects are selected. Mixed
selection suppresses current-role feedback. Incompatible roles stay in their
fixed positions but are dimmed and cannot be selected.

## Gesture and geometry

Press enters `PRESSED`; a 180 ms hold or movement beyond 25 UI pixels opens the
fan. `RADIAL_OPEN` and `TARGET_HOVER` track the cursor. Release recalculates the
sector from its actual position and returns a role once (`COMMIT`) or cancels
(`CANCEL`). The session is then removed, representing idle. No assignment occurs
on press or on cancellation.

Five equal angular wedges occupy an upward half-circle. The ordered roles run
counterclockwise from the right. Selection requires a radius of 30–112 UI pixels;
the center and the opposite half-circle are neutral. Visible bubbles sit at
radius 84.7 with an independent radius of 18. Releasing at radius 35 therefore
selects a wedge without touching its bubble. The active bubble grows 12% and
uses the toolbar cyan accent. The five supplied SVGs are preserved unchanged in
`assets/quick_assign_icons`; `tools/build_quick_assign_icons.py` renders dedicated
128px runtime PNGs. Transparent margins are normalized; white RGB and luminance-
derived alpha preserve the details on dark and blue bubbles. Rendering requires Pillow, resvg-py
and system fonts (the Collider SVG contains text). The central button continues
to use the existing Add icon. Other UI role icons remain unchanged.

The selected wedge fades from blue near the center toward the outer radius,
using exactly the same angular boundaries as hit testing. Its uppercase role
caption is rotated along the wedge and flipped on the left to remain upright;
Rigid Body uses the compact caption RBD. Diagnostic/error messages and their clickable
log icon sit to the right of Bake, outside the toolbar pill.

Layout prefers upward, then downward, rightward or leftward orientations according
to clipping. Entire-layout quarter turns preserve ordering and rotate hit testing
with rendering. If no orientation fits a very small region, both presentation and
interaction geometry shrink uniformly. Layout remains fixed during a gesture;
viewport resizing or UI-scale changes cancel rather than move the targets.

Each live operator owns its gesture, timer and original context. A registry keyed
by window/region identifies only live sessions for drawing and teardown. Release,
Esc, RMB, focus loss, object/selection/context changes and invalid regions cancel.
Load-pre, F6, preference disable and unregister clear sessions/timers; the existing
toolbar pulse also prunes closed-window sessions. Blender retires invalidated modal
operators on its next event. The feature adds no idle timer or draw handler.

## Validation

The 2.7.0 full suite passed with 1,825 tests before the final caption checks;
the final focused Quick Assign run passed all 32 cases. External solver tests
require separately configured fixtures. Real Blender 5.2.2 registration and
live-event checks passed with the compact geometry and upright captions.

Run pure/adapter regressions:

```text
python -m pytest tests/test_quick_assign.py tests/test_floating_simulation.py tests/test_phase28_physics_integration.py tests/test_imports.py -q
```

Run actual Blender registration and dispatch checks (three registration cycles,
idempotent repeated calls, incompatible-role rejection and active-only assignment
with multiple selected meshes):

```text
blender --background --factory-startup --python-exit-code 1 --python tools/blender_quick_assign_smoke.py
```

Run live event verification in a disposable factory scene:

```text
blender --factory-startup --enable-event-simulate --python tools/blender_quick_assign_smoke.py -- --events
```

Append `--screenshot ABSOLUTE_PATH.png` to capture the active fan. Use
`-- --interactive` instead of `-- --events` for hands-on review. The script never
saves preferences or a scene and never invokes Bake. Event test failures exit
nonzero; successful runs print the live-check success marker.

Verified with Blender 5.2.2 LTS: press/hold, visible icons, sector selection away
from bubbles, release assignment, one-step Undo, center/outside/Esc/RMB cancel,
short clicks, repeated interactions and disabling/re-enabling during a gesture.
Actual viewport screenshots were inspected. These were scripted live events, not
a complete human-driven pass through all requested manual checks.

Unit coverage additionally exercises UI scales 0.75–2, edge rotations, small-region
fitting, sector boundaries, target switching, disabled sectors, mixed-role feedback,
empty layouts, cancellation, timer cleanup and deterministic ordering.

Remaining hands-on checks: workspace/window closing and file reload during use,
physical HiDPI displays and subjective hold/drag feel. Edge rotation and scaling
have automated geometry coverage; not every orientation was exercised in the live
viewport.

The external solver is not modified or bundled.
