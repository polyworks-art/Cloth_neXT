# PATTERN NeXt

PATTERN NeXt is the pattern authoring workspace of Cloth NeXt: draw garment
panels in a 2D pattern editor, get a filled, triangulated cloth mesh at once,
sew the panels and arrange them in 3D, all inside Blender and inside the
Cloth NeXt extension. There is nothing extra to install.

## Installation

PATTERN NeXt ships with Cloth NeXt (same extension, same `cloth_next` id).
Enable Cloth NeXt as usual. In every interactive Blender session the
**PATTERN NeXt** tab appears in the workspace bar next to Layout, Modeling,
Scripting and the others. Enabling Cloth NeXt never switches workspaces;
click the tab when you want it.

If the tab was deleted, **Window > Open PATTERN NeXt** restores and opens it
(F3 search: "Open PATTERN NeXt"). Disabling Cloth NeXt leaves the tab and all
pattern data in the file untouched.

## The workspace

The workspace has exactly two editors (no Outliner or Properties column;
Cloth NeXt physics of a panel stays reachable in any other workspace or by
turning an area into a Properties editor).

| Area | What it is |
| --- | --- |
| Left | A normal Blender 3D View: orbit, pan, zoom, perspective/orthographic, selection, Move/Rotate/Scale, materials. Your avatar and the generated garment panels live here. |
| Right | The **2D Pattern editor**: front orthographic, permanently. Pan (Shift+Middle mouse, trackpad) and zoom (wheel, Ctrl+Middle mouse) work; orbit, roll, numpad views, perspective toggle and camera view do not. |
| Inspector | A docked column on the right edge of the 2D editor (not Blender's N sidebar): the pattern list (visibility, selection) and the properties of what is selected: overview, pattern, several patterns, point, edge or seam. Click a value to edit it with Blender's own field. I (or the toolbar's inspector button) shows/hides it. |
| 3D header | "Arrange" menu: place selected panels in front/behind, reset to staging, seam display. |

The 2D editor draws patterns as flat shapes with outline, fill and
triangulation on a centimetre grid. Scene objects are hidden in it, so the
3D arrangement never clutters the drafting table.

### How the lock works

The 2D editor is a 3D View with Blender's own rotation lock (the quad-view
"Lock" flag), stored in the .blend file. With it, Blender itself refuses
orbit, roll, numpad 1/3/7/9, perspective toggle (numpad 5) and camera view
(numpad 0); pan and zoom keep working. It is set only on the right view of
the PATTERN NeXt workspace; all other 3D views are untouched. If the view is
ever found not front orthographic (for example changed by a script), it is
restored on the next redraw.

### Coordinates

The 2D canvas is Blender's front (XZ) plane: canvas `(u, v)` in metres is
world `(u, 0, v)`; +u is right, +v is up, looking along +Y. Pattern points
are stored in pattern-local coordinates; each pattern has its own 2D
position and rotation on the canvas.

## Tools

The tool strip at the top of the 2D editor is grouped Create · Edit ·
Construct · Display (Grid, Snap, Measurements, Triangulation, Sewing
Display), with Inspector and Frame All on the right. Hover a button for its
name, purpose and shortcut. On a narrow editor the Display group folds into
one button that opens a popover, and the groups wrap onto further rows.
Keys while the mouse is over the 2D editor:

| Tool | Key | Use |
| --- | --- | --- |
| Select / Move | Q | Click a pattern to select (Shift adds), drag to move, drag on empty canvas for box select. R rotates (Shift: 15° steps), Shift+D duplicates, X/Delete deletes, Ctrl+C / Ctrl+V copy and paste at the mouse, A selects all, Alt+A none. |
| Polygon | H | Click points. Click-and-drag places a smooth curve point. Click the first point or press Enter to close. Backspace removes the last point, Esc cancels, right-click finishes. |
| Rectangle | S | Drag (Shift: square). A click without dragging asks for exact width and height. |
| Ellipse | E | Drag (Shift: circle). A click asks for exact size. |
| Curve | C | Click points of a smooth closed outline; each point gets tangents from its neighbours (drag while placing a point to set its tangent yourself). |
| Edit Pattern | Z | Drag points or edges, Shift-click to select several points, X removes selected points, double-click an edge to add a point. Right-click an edge: Add Point Here, Make Curved/Straight, Set Edge Length. |
| Edit Curve | V | Drag an edge to bend it into a Bezier curve, drag the tangent handles, Alt-click a point to toggle smooth/corner. |
| Internal Shape | G | Inside a pattern, draw a hole (closed) or an internal line (L switches; Enter or double-click ends a line). |
| Sewing | M | Click an edge, then the edge to sew it to. Click near the ends that belong together; the seam direction follows. F flips the hovered seam, X deletes it. |

Also: Home frames all patterns, Numpad . or F frames the selection, I
toggles the inspector, number keys 1-9 pick the tools in toolbar order. Shift constrains angles to 45°
steps while drawing; Ctrl temporarily disables snapping. N keeps opening the
sidebar (Blender's shortcut is kept; Sewing therefore uses M).

Snapping (toolbar, right side): grid, pattern points, pattern edges. Points
snap to other patterns and to the draft itself. The grid step follows the
zoom (1/2/5 steps) unless a fixed Grid Step is set.

Measurements: segment lengths of the selected pattern (outside the outline,
only where they fit), of the draft and of the rectangle/ellipse being
dragged are shown in centimetres. Selecting a point or an edge (Edit
Pattern) shows its coordinates or exact length in the inspector, where it
can be typed.

## Automatic fill and triangulation

Closing any outline creates the pattern and its mesh in one step: there is
no Fill command and no Edit Mode. Every edit that changes the shape (points,
edges, curves, holes, lines, rectangle/ellipse size, Particle Distance)
regenerates only that pattern's mesh. Small patterns remesh live while
dragging; larger ones show a live outline preview and remesh on release.
An edit that would make the outline invalid (self-intersection, duplicate
points, almost no area, a hole outside the pattern) is shown in red and
rejected; the previous shape and mesh stay.

**Particle Distance** (per pattern, default 20 mm) is the target edge length
of the cloth triangles. The mesher

1. resamples outline, holes and internal lines at Particle Distance,
   refining curves until every boundary edge stays within **1 mm** of the
   analytic Bezier curve;
2. seeds interior particles on a hexagonal lattice of the same spacing,
   keeping 0.6 × Particle Distance clearance from the boundary;
3. triangulates with Blender's exact constrained Delaunay
   (`mathutils.geometry.delaunay_2d_cdt`), relaxes interior particles and
   triangulates again.

The result covers exactly the pattern (holes excluded), conforms to the
boundary and to internal lines, is consistently wound (normals face the
front), and has no zero-area triangles. Typical minimum angles are above
26°; only shapes that are themselves thinner than Particle Distance get
thinner triangles.

## The generated panels

Each pattern owns one real mesh object in the **PATTERN NeXt** collection.
It is a regular Cloth NeXt **Cloth** object (Cloth NeXt enabled, simulation
modifier, persistent export identity), so materials, colliders, validation,
Live Simulation and Bake treat it like any other cloth.

* Mesh: the flat rest shape, pattern-local `(u, v)` -> object-local `(u, 0, v)`.
* New panels appear in a staging area in front of the origin
  (Y = Staging Depth, default -0.5 m), laid out like the 2D canvas.
* Moving, rotating or scaling the panel in 3D (its arrangement) never
  changes the pattern. Moving or rotating the pattern on the 2D canvas
  never moves the panel in 3D.
* UV map "PatternUV" = canvas position in metres (1 UV unit = 1 m), so a
  texture runs continuously across panels laid out on the canvas, and the
  2D rotation is the grain direction. Moving a pattern on the canvas only
  rewrites its UVs; editing the shape rewrites mesh and UVs together.
* Arrangement (sidebar): Front / Back placement and Reset Arrangement
  (back to the staging layout).
* The panel mesh is derived data: editing it in Edit Mode is overwritten by
  the next pattern edit.

Selection is shared: the selected patterns are the selected panel objects.
Clicking a pattern in 2D selects its panel in 3D, selecting a panel in 3D
highlights its pattern in 2D.

## Sewing

A seam side is a stretch of outline from one pattern point to another,
identified by the points' stable ids, never by mesh vertex indices.
Inserting points into the stretch or changing Particle Distance keeps the
seam. After every remesh the seam is re-derived into a regular Cloth NeXt
**sewing definition** (the same data the Cloth NeXt Sewing editor creates and
the solvers read), so there is no second sewing system. Removing a sewn
point marks the seam as needing attention instead of silently dropping it.

Seams are drawn in the 2D editor as coloured edges with drafting notches.
Direction chevrons and three dashed connectors appear only for seams of
the selected patterns, the hovered seam or the seam chosen in the Sewing
tool, so a garment with many seams stays readable; a length difference
over 2 % is labelled. Clicking a seam selects it (inspector: lengths,
difference, Crossed, Delete). While sewing, the first edge and a dashed
preview of how it pairs with the hovered edge are shown before the second
click. Simulating seams with AETHER is the next milestone;
PATTERN NeXt does not run a simulation itself.

## Symmetry

Mirror (sidebar or right-click) creates a mirrored copy next to the pattern.
With Linked Symmetry on, every outline or curve edit of either pattern is
mirrored onto the other (same point ids, so seams on both stay valid); each
keeps its own 2D position and 3D arrangement. Unlink Symmetry ends the link.

## Saving, undo, versions

Patterns, seams and display settings are Scene data saved in the .blend.
Each completed edit is one undo step (Ctrl+Z / Ctrl+Shift+Z), restoring the
pattern, its panel and the derived sewing together. The data carries a
schema version (`cloth_next.pattern_next.SCHEMA_VERSION`); newer versions
add forward-only migration steps in `blender/pattern_next/model.py:migrate`,
run on load.

## For developers

* Geometry core (no `bpy`, unit-tested): `cloth_next/pattern_next/`
  (`shapes`, `curves`, `validation`, `mesher`, `editing`, `seams`,
  `snapping`, `quality`, and `delaunay`, a pure fallback for tests).
* Blender integration: `cloth_next/blender/pattern_next/` (`properties`,
  `model`, `sync`, `workspace`, `style` (design system), `draw`, `toolbar`,
  `dock` (inspector), `inspector` (selection context), `editor`,
  `operators`, `ui`), registered through `cloth_next/blender/registration.py`.
* Icons: `assets/pattern_next_icons/*.svg` (Cloth NeXt line style), rendered
  by `python tools/build_pattern_icons.py` to
  `cloth_next/assets/icons/pattern_next/` at 18/27/36/64 px.
* Workspace template: `cloth_next/resources/pattern_next/pattern_workspace.blend`,
  rebuilt with `blender --factory-startup --python tools/build_pattern_workspace.py`
  (windowed; screen operators need a window). It is appended with
  `bpy.data.libraries.load`, which adds the tab without activating it;
  `workspace.duplicate()` and `append_activate()` would both switch the
  user's workspace.
* The 2D editor input is one modal session per window (the Cloth NeXt modal
  editor convention), started automatically while the window shows the
  workspace; it only consumes events over the 2D editor.
* `blender/pattern_next/adapter.py` returns an immutable `GarmentSnapshot`
  (flat rest meshes, triangles, UVs, arrangement matrices, materials, seam
  vertex correspondence): the boundary for the AETHER fitting milestone.
  Simulation itself already goes through the regular Cloth NeXt pipeline.
* Existing Cloth NeXt 3D overlays (Sewing, Attachments, Linked Colliders,
  wind, water, TETHYS, the simulation toolbar) skip the 2D editor.
* Tests: `tests/test_pattern_next_geometry.py`;
  `tools/blender_pattern_geometry_check.py` (real Blender meshing, quality
  and benchmarks); `tools/blender_pattern_gui_acceptance.py` (real GUI with
  simulated input, two Blender sessions). See
  `docs/validation/PATTERN_NEXT_WORKSPACE_REPORT.md`.
