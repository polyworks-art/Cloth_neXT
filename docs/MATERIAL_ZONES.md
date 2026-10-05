# Material Zones

Material Zones are at the bottom of **Physics Properties → Cloth NeXt →
Material**, for Cloth objects. Each face has one owner: Base Material or one
zone. Adjacent faces may share vertices without sharing material values.
This feature does not use vertex groups, weights, falloff, or boundary blends.

Add a zone, give it a name, and use **Add Property** to choose an override.
The menu offers only properties not already present in that zone. A removed
override inherits the base value. Disabling a zone inherits all base values
while retaining its selection. Face counts are refreshed by authoring and
validation operations, rather than expensive panel redraws.

Supported properties:

| Property | Solver key | Artist units / range |
|---|---|---|
| Stretch Resistance | `young-mod` | Positive, at most 1e9 |
| Bend Resistance | `bend` | Nonnegative |
| Friction | `friction` | 0–1, with the existing CNX calibration to solver 0–0.5 |
| Shape Damping | `deformation-damping` | Nonnegative |
| Fold Damping | `bending-damping` | Nonnegative |

All numbers must remain finite and representable as float32. Zone names do
not apply fabric presets. The existing base material presets keep working;
zone overrides retain their explicit values when the base preset changes.

Pressure, Sewing, Shrink, Collision Gap, Surface Offset, Solver Model,
Density/Surface Weight, and Poisson/Sideways Response inherit the existing
object-wide controls. Stretch Limit, plasticity, and anisotropic bending are
also outside this first tested zone property set. This is a deliberately
limited UI, not a claim that every omitted key is inherently global in PPF.

## Edit Selection

Use **Edit Selection** in Object Mode with a 3D Viewport open. The tool uses
the invoking 3D Viewport, or the first 3D Viewport when started from Properties.

- Hold/drag **LMB** to assign visible face surfaces inside the circle.
- **Shift + LMB** removes faces from this zone and returns them to Base.
- The **mouse wheel** changes only the brush radius.
- **Enter** commits the staged assignment; **Esc** discards it.
- Use the usual middle-mouse viewport navigation to orbit and pan.

Small points identify face centers; larger blue points show visible centers
under the brush. Assigned faces have a translucent blue overlay. Polygon
overlap alone does not select a face: surface samples must hit visible geometry.
Visible centers supplement these hits for small faces. For non-planar or concave
polygons, center markers are projected onto the polygon's tessellated surface.

There is **no selection through cloth folds or other visible geometry** and
no Select Through toggle. Blender's X-Ray setting does not authorize selection
of hidden faces. The tool tests the first evaluated BVH surface hit, including
the target polygon identity, independently of GPU display depth. It does not
reject a surface because its normal faces away from the camera. Perspective
and orthographic projection are supported. The overlay establishes its own
surface depth even in X-Ray views.

Selecting a face into another zone transfers ownership. Removing it does not
restore a previous owner. Deleting a zone returns its faces to Base. Reordering
the collection changes neither ownership nor physical results.

## Topology and solver requirements

Face ownership is saved as a Blender FACE-domain integer mesh attribute;
zone definitions, UUIDs and cached counts are saved as RNA properties. Mesh
identity and an ordered connectivity digest protect against mesh replacement
and topology changes. Connectivity changes refuse editing/Bake until you use
**Clear All Zone Selections** and select faces again. This operation preserves
zone names/properties and resets all selections. Make shared or linked meshes
local and single-user before authoring. Vertex deformation with unchanged
connectivity is supported. A topology-modified export is refused when its
triangles cannot be matched exactly to the authored mesh.

Every exported loop triangle inherits its source Blender polygon's owner.
Quads and N-gons therefore apply the same zone to all their child triangles.
The result uses direct triangle parameter arrays, never averaged vertex maps.
Only keys with enabled overrides are emitted. Other properties stay inherited.
If Friction Regions already exist, their existing triangle friction is inherited
where a zone does not explicitly override friction.

Material Zones require a compatible **managed Cloth NeXt solver**. The older
verified frontends use CNX's generalized triangle-friction overlay; official
Gaia 0.23 uses CNX's existing native-input bridge without modifying frontend
code. External/development installations are refused by the Blender authoring
bridge at Bake time. There is no blended fallback. Unsupported/invalid data
refuses the Bake with a remediation message before simulation starts.

Adding/removing zones or overrides, changing enabled state/name/value, and
committing selection mark validation dirty. The ownership digest and zone
definitions participate in cache/settings fingerprints. Authoring controls
and operators are disabled/refused while a Bake owns the scene.

## Validation

See [implementation and validation report](MATERIAL_ZONES_VALIDATION.md).
The mandatory regression uses adjacent triangles sharing two vertices:
base bend **10.0**, zone bend **100.0**. Both the managed scalar expansion and
the real official frontend's native input binder preserve these exact values.

Selection displays the mesh immediately before the Cloth NeXt modifier. Downstream modifiers such as Solidify and Subdivision are temporarily hidden and restored on confirmation, cancellation or cleanup. They remain valid after Cloth NeXt. Upstream topology must still map to the authored faces. Surface hits also select large or partly visible faces whose center is outside the brush.
