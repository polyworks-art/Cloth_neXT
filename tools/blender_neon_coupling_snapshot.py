"""Read saved NEON settings and animated transforms without saving the source."""
import json
import sys
from pathlib import Path
import bpy
import addon_utils
from dataclasses import asdict
import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
addon_utils.enable('cloth_next', default_set=True)
repos = bpy.context.preferences.extensions.repos
for module in ('superhivemarket_com', 'api_portal_polyhammer_com'):
    if not any(r.module == module for r in repos):
        repos.new(name=module, module=module)
for name in (() if '--settings-only' in sys.argv else ('bl_ext.superhivemarket_com.flip_fluids_addon',
             'bl_ext.api_portal_polyhammer_com.character_dna',
             'bl_ext.api_portal_polyhammer_com.character_assembly')):
    addon_utils.enable(name, default_set=True)
bpy.ops.wm.open_mainfile(filepath='F:/16_Cloth_neXt/SW04/SW04_NEON.blend')
if '--settings-only' in sys.argv:
    from cloth_next.blender.object_properties import shell_settings_from
    from cloth_next.ppf.schema.params import shell_wire_params
    from cloth_next.blender.material_zones import zones_of, storage_owners, export_tables
    records = []
    for obj in bpy.context.scene.objects:
        s = getattr(obj, 'cloth_next', None)
        if s and s.enabled and s.role == 'CLOTH':
            material = shell_settings_from(s)
            obj.data.calc_loop_triangles()
            geometry_path = root / ('docs/validation/gaia026/coupling-investigation/' + obj.name + '-mesh.npz')
            np.savez(geometry_path, triangles=np.asarray([list(t.vertices) for t in obj.data.loop_triangles]),
                edges=np.asarray([list(e.vertices) for e in obj.data.edges]),
                rest=np.asarray([list(v.co) for v in obj.data.vertices]))
            weights = []
            group = obj.vertex_groups.get(s.pin_group) if s.pin_group else None
            if group:
                weights = [g.weight for v in obj.data.vertices for g in v.groups if g.group == group.index]
            records.append(dict(name=obj.name, preset=s.material.preset,
                material=asdict(material), wire=shell_wire_params(material),
                face_material_params=export_tables(obj, tuple(tuple(t.vertices) for t in obj.data.loop_triangles), material),
                zones=[asdict(z) for z in zones_of(obj)], zone_face_owners=list(storage_owners(obj)),
                pinning=dict(enabled=s.pinning_enabled, group=s.pin_group,
                    mode=s.pin_mode, weighted_vertices=len(weights),
                    min_weight=min(weights) if weights else None, max_weight=max(weights) if weights else None),
                matrix_world=[list(row) for row in obj.matrix_world],
                object_constraints=[dict(name=c.name, type=c.type, influence=c.influence) for c in obj.constraints],
                settings={k: getattr(s,k) for k in (
                'water_flow_container', 'water_flow_enabled', 'water_flow_influence',
                'water_flow_velocity_scale', 'water_flow_coupling_strength', 'water_flow_native_flow')}))
    out = root / 'docs/validation/gaia026/coupling-investigation/saved-settings.json'
    out.write_text(json.dumps(records, indent=2))
    bpy.ops.wm.quit_blender()
    raise SystemExit(0)
from bl_ext.api_portal_polyhammer_com.character_dna.runtime import controller, engine
controller.register()
controller.after_load()
assert engine.NAMESPACE in bpy.app.driver_namespace
for obj in bpy.context.scene.objects:
    if obj.animation_data:
        for curve in obj.animation_data.drivers:
            if engine.NAMESPACE in curve.driver.expression:
                curve.driver.expression = curve.driver.expression
        obj.update_tag()
scene = bpy.context.scene
records = []
for obj in scene.objects:
    s = getattr(obj, 'cloth_next', None)
    if not s or not s.enabled or s.role != 'CLOTH':
        continue
    records.append(dict(name=obj.name, vertices=len(obj.data.vertices),
        settings={key: getattr(s, key) for key in ('water_flow_enabled',
            'water_flow_container', 'water_flow_influence', 'water_flow_velocity_scale',
            'water_flow_coupling_strength', 'water_flow_native_flow')},
        matrices=[]))
for frame in range(1, 251):
    scene.frame_set(frame)
    for record in records:
        record['matrices'].append([list(row) for row in scene.objects[record['name']].matrix_world])
result = dict(fps=scene.render.fps / scene.render.fps_base,
              unit_scale=scene.unit_settings.scale_length, objects=records)
out = root / 'docs/validation/gaia026/coupling-investigation'
out.mkdir(exist_ok=True)
(out / 'scene-snapshot.json').write_text(json.dumps(result, indent=2))
print('NEON_COUPLING_SNAPSHOT_COMPLETE', flush=True)
