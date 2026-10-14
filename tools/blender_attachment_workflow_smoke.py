"""Real-Blender smoke for persistent artist Attachment data and lifecycle."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy


def mesh_object(name, offset):
    vertices = [(x + offset, y, z) for x, y, z in (
        (-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0))]
    mesh = bpy.data.meshes.new(f"{name}Mesh")
    mesh.from_pydata(vertices, (), ((0, 1, 2, 3),))
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def entry(obj):
    obj.data.calc_loop_triangles()
    return SimpleNamespace(
        obj=obj, role=obj.cloth_next.role,
        boundary_vertices=tuple(tuple(vertex.co) for vertex in obj.data.vertices),
        boundary_triangles=tuple(tuple(triangle.vertices)
                                  for triangle in obj.data.loop_triangles))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--blend", required=True)
    args, _unknown = parser.parse_known_args(sys.argv[sys.argv.index("--") + 1:])
    root = Path(args.repo).resolve()
    sys.path.insert(0, str(root))

    import cloth_next
    from cloth_next.blender import object_attachments

    cloth_next.register()
    try:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        source = mesh_object("Attachment Source", 0.0)
        target = mesh_object("Attachment Target", 3.0)
        for obj in (source, target):
            obj.cloth_next.enabled = True
            obj.cloth_next.role = "CLOTH"
        # A complete ring needs no target selection in Smart Attach. Multiple
        # sources may share their actual nearest target vertex.
        smart = object_attachments._commit_attachment(
            bpy.context.scene, source, target, (3, 2, 1, 0), (), smart=True)
        assert len(smart.points) == 4
        for point in smart.points:
            nearest = min(range(4), key=lambda index: (
                (source.matrix_world @ source.data.vertices[point.source_index].co
                 - target.matrix_world @ target.data.vertices[index].co).length_squared,
                index))
            assert point.target_triangle[tuple(point.target_weights).index(1.0)] == nearest
        assert len(object_attachments.snapshot_enabled(
            bpy.context.scene, (entry(source), entry(target)))) == 1
        bpy.context.scene.cloth_next_object_attachments.remove(0)
        item = object_attachments._commit_attachment(
            bpy.context.scene, source, target, (3, 0, 1), (2, 0, 3))
        assert len(item.points) == 3
        assert tuple(row.index for row in item.source_vertices) == (0, 1, 3)
        assert tuple(row.index for row in item.target_vertices) == (0, 2, 3)
        assert item.target_object == target
        # Exercise redraw reuse without requiring a GPU context in background
        # Blender; all RNA/depsgraph identities remain real Blender data.
        from unittest.mock import patch
        shader = SimpleNamespace(bind=lambda: None, uniform_float=lambda *_: None)
        builds = []
        def batch_for_shader(*args, **kwargs):
            builds.append(args[1])
            return SimpleNamespace(draw=lambda *_: None)
        import gpu
        import gpu_extras.batch
        target.cloth_next.role = "COLLIDER"
        object_attachments._clear_overlay_cache()
        with patch.object(gpu.shader, "from_builtin", return_value=shader), \
                patch.object(gpu_extras.batch, "batch_for_shader", batch_for_shader), \
                patch.object(gpu.state, "point_size_set"), \
                patch.object(object_attachments, "_collider_preview_vertices",
                             wraps=object_attachments._collider_preview_vertices) as preview:
            for _ in range(100):
                object_attachments._draw_overlay()
            assert builds == ["LINES", "POINTS"], builds
            assert preview.call_count == 1
            unrelated = bpy.data.objects.new("Unrelated", None)
            graph = SimpleNamespace(updates=[SimpleNamespace(id=unrelated)])
            object_attachments._invalidate_overlay(bpy.context.scene, graph)
            object_attachments._draw_overlay()
            assert len(builds) == 2
            target.name = "Renamed Attachment Target"
            graph.updates = [SimpleNamespace(id=target)]
            object_attachments._invalidate_overlay(bpy.context.scene, graph)
            object_attachments._draw_overlay()
            assert len(builds) == 4
            bpy.context.scene.frame_set(2)
            object_attachments._draw_overlay()
            assert len(builds) == 6
            assert preview.call_count == 3
            item.show_overlay = False
            object_attachments._draw_overlay()
            assert object_attachments._overlay_cache["entry"][2] is None
            item.show_overlay = True
        target.cloth_next.role = "CLOTH"
        object_attachments._clear_overlay_cache()
        target.name = "Renamed Attachment Target"
        assert item.target_object == target
        item.stiffness = 2.75
        snapshot = object_attachments.snapshot_enabled(
            bpy.context.scene, (entry(source), entry(target)))
        assert snapshot[0][0].stiffness == 2.75
        blend = Path(args.blend).resolve()
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        source = bpy.data.objects["Attachment Source"]
        target = bpy.data.objects["Renamed Attachment Target"]
        items = bpy.context.scene.cloth_next_object_attachments
        assert len(items) == 1 and items[0].target_object == target
        assert items[0].stiffness == 2.75
        items.remove(0)
        assert not tuple(item for item in items
                         if item.target_persistent_id == target.cloth_next.persistent_export_id)
        bpy.ops.ed.undo_push(message="Attachment smoke deletion")
        print("ATTACHMENT_WORKFLOW_SMOKE_OK")
    finally:
        cloth_next.unregister()
        cloth_next.register()
        cloth_next.unregister()


if __name__ == "__main__":
    main()
