"""Blender runtime regression: live preview, rollback, and final cache handoff."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
from cloth_next.blender import registration, solver_test as module
from cloth_next.bake import pc2


def coordinate(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return round(evaluated.data.vertices[0].co.x, 3)


registration.register()
try:
    with tempfile.TemporaryDirectory(prefix="clothnext-live-regression-") as temporary:
        root = Path(temporary)
        for index, name in enumerate(("cn_test_cloth_old.pc2",
                                     ".cn_test_cloth_old.pc2.cancel.tmp",
                                     "recovery/partials/old.pc2.partial")):
            mesh = bpy.data.meshes.new(f"audit-{index}")
            mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
            obj = bpy.data.objects.new(f"audit-{index}", mesh)
            bpy.context.collection.objects.link(obj)
            old = root / str(index) / name
            live = old.parent / ".cn_test_cloth_new.pc2.live.tmp"
            final = old.parent / "cn_test_cloth_new.pc2"
            def frames(x):
                return [[(x, 0, 0), (x + 1, 0, 0), (x, 1, 0)]] * 3
            pc2.write_pc2(old, frames(10))
            pc2.write_pc2(live, frames(20))
            header = pc2.write_pc2(final, frames(30))
            mod = obj.modifiers.new(module.import_result.MODIFIER_NAME, 'MESH_CACHE')
            module._configure_playback_modifier(mod, 1)
            mod.filepath = str(old)
            module.mark_owned_playback(obj, mod, str(old))
            identity = tuple(tuple(row) for row in obj.matrix_world)
            target = module.DeformablePlan(tuple(tuple(v.co) for v in mesh.vertices),
                identity, obj.name, f'uuid-{index}', final, '', {}, 'CLOTH')
            plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(),
                target.initial_local, identity, obj.name, root, final, 3,
                frame_start=1, frame_end=3, deformables=(target,))
            bpy.context.scene.frame_set(1)
            assert coordinate(obj) == 10, coordinate(obj)
            module._hide_previous_playback(plan)
            assert coordinate(obj) == 0, coordinate(obj)
            module._advance_bake_timeline(plan, 2, {target.uuid: str(live)})
            assert coordinate(obj) == 20, coordinate(obj)
            assert old.is_file()
            module._restore_live_playback()
            assert coordinate(obj) == 10, coordinate(obj)
            module._advance_bake_timeline(plan, 3, {target.uuid: str(live)})
            module._attach_playback(plan, header)
            assert coordinate(obj) == 30, coordinate(obj)
            assert not module._live_playback_records
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            print(f'LIVE CACHE PASS: {name}')
finally:
    registration.unregister()
print('Live cache regression passed: rebake, cancelled cache, recovery partial')
