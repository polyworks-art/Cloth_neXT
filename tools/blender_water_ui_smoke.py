"""Verify Water Flow panel controls against real Blender RNA, without saving a scene."""
from pathlib import Path
from types import SimpleNamespace
import sys
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender.water_flow import CLOTHNEXT_PT_water_flow

controls = set()
operators = set()


class Layout:
    enabled = True

    def column(self):
        return self

    def prop(self, owner, name):
        assert name in owner.bl_rna.properties, name
        controls.add(name)

    def label(self, **kwargs):
        pass

    def operator(self, identifier):
        assert identifier == 'cloth_next.prepare_water_flow'
        assert bpy.ops.cloth_next.prepare_water_flow.get_rna_type()
        operators.add(identifier)


cloth_next.register()
try:
    obj = bpy.data.objects.new('Water Flow UI Smoke', bpy.data.meshes.new('Water Flow UI Smoke'))
    bpy.context.scene.collection.objects.link(obj)
    obj.cloth_next.enabled = True
    obj.cloth_next.role = 'CLOTH'
    obj.cloth_next.water_flow_enabled = True
    obj.cloth_next.water_flow_show_vectors = True
    context = SimpleNamespace(object=obj)
    assert CLOTHNEXT_PT_water_flow.poll(context)
    assert bpy.types.CLOTHNEXT_PT_water_flow.bl_context == 'physics'
    CLOTHNEXT_PT_water_flow.draw(SimpleNamespace(layout=Layout()), context)
    assert controls == {
        'water_flow_enabled', 'water_flow_domain', 'water_flow_influence',
        'water_flow_velocity_scale', 'water_flow_resolution', 'water_flow_container',
        'water_flow_show_vectors', 'water_flow_vector_stride', 'water_flow_vector_scale',
    }, controls
    assert operators == {'cloth_next.prepare_water_flow'}
    print('WATER FLOW UI PASS: registered Physics panel, nine RNA controls and preparation operator')
finally:
    cloth_next.unregister()
assert not hasattr(bpy.types, 'CLOTHNEXT_PT_water_flow')
print('WATER FLOW UI PASS: registration cleanup')
