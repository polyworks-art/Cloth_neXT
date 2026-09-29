"""Run with factory-startup Blender to verify persistent group attachments."""
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender import object_attachments as attachments
from cloth_next.attachments import AttachmentError, wire_entry

cloth_next.register()


def mesh(name, role, z):
    data = bpy.data.meshes.new(name)
    data.from_pydata([(0, 0, z), (1, 0, z), (0, 1, z)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.cloth_next.enabled = True
    obj.cloth_next.role = role
    group = obj.vertex_groups.new(name="Attach")
    group.add([0, 1], 1, 'REPLACE')
    return obj


def entries(scene):
    return tuple(SimpleNamespace(obj=obj, role=obj.cloth_next.role,
        boundary_vertices=attachments._mesh_triangles(obj)[0],
        boundary_triangles=attachments._mesh_triangles(obj)[1])
        for obj in scene.objects if obj.cloth_next.enabled)


for source_role, target_role in [('CLOTH', 'CLOTH'), ('CLOTH', 'SOFT_BODY'),
                                  ('SOFT_BODY', 'CLOTH'), ('SOFT_BODY', 'SOFT_BODY')]:
    source = mesh('Source', source_role, 0)
    target = mesh('Target', target_role, 1)
    bpy.context.view_layer.objects.active = source
    source.select_set(True)
    assert bpy.ops.clothnext.add_group_attachment() == {'FINISHED'}
    scene = bpy.context.scene
    item = scene.cloth_next_object_attachments[-1]
    item.target_object = target
    item.source_group = item.target_group = 'Attach'
    assert bpy.ops.clothnext.bind_group_attachment(index=len(scene.cloth_next_object_attachments)-1) == {'FINISHED'}
    original = [(p.source_index, tuple(p.target_triangle), tuple(p.target_weights)) for p in item.points]
    target.location.x += 10
    records = attachments.snapshot_enabled(scene, entries(scene))
    assert original == [(p.source_index, tuple(p.target_triangle), tuple(p.target_weights)) for p in item.points]
    relation, source_count, target_count = records[-1]
    assert wire_entry(relation, source_vertex_count=source_count,
                      target_vertex_count=target_count)['ind']
    item.enabled = False

item.enabled = True
bpy.ops.ed.undo_push(message='Attachment bound')
item.enabled = False
bpy.ops.ed.undo_push(message='Attachment disabled')
assert bpy.ops.ed.undo() == {'FINISHED'}
assert bpy.context.scene.cloth_next_object_attachments[-1].enabled
assert bpy.ops.ed.redo() == {'FINISHED'}
assert not bpy.context.scene.cloth_next_object_attachments[-1].enabled
bpy.context.scene.cloth_next_object_attachments[-1].enabled = True
with tempfile.TemporaryDirectory(prefix='cloth-next-group-smoke-') as tmp:
    blend = str(Path(tmp) / 'attachments.blend')
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    item = scene.cloth_next_object_attachments[-1]
    assert item.target_object is not None
    assert original == [(p.source_index, tuple(p.target_triangle), tuple(p.target_weights)) for p in item.points]
    assert len(attachments.snapshot_enabled(scene, entries(scene))) == 1
    target = item.target_object
    target.vertex_groups['Attach'].remove([1])
    try:
        attachments.snapshot_enabled(scene, entries(scene))
        raise AssertionError('Changed group accepted without rebinding')
    except AttachmentError:
        pass
    assert original == [(p.source_index, tuple(p.target_triangle), tuple(p.target_weights)) for p in item.points]
    assert bpy.ops.clothnext.bind_group_attachment(index=len(scene.cloth_next_object_attachments)-1) == {'FINISHED'}
    target.vertex_groups['Attach'].name = 'Renamed'
    try:
        attachments.snapshot_enabled(scene, entries(scene))
        raise AssertionError('Renamed group accepted')
    except AttachmentError:
        pass
    item.target_group = 'Renamed'
    assert bpy.ops.clothnext.bind_group_attachment(index=len(scene.cloth_next_object_attachments)-1) == {'FINISHED'}
    bpy.data.objects.remove(target, do_unlink=True)
    try:
        attachments.snapshot_enabled(scene, entries(scene))
        raise AssertionError('Deleted target accepted')
    except AttachmentError:
        pass
cloth_next.unregister()
print('GROUP_ATTACHMENT_SMOKE_PASSED')
