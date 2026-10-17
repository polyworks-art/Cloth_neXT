"""Verify Water Flow panel controls against real Blender RNA, without saving a scene."""
from pathlib import Path
from types import SimpleNamespace
import sys
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender.water_flow import CLOTHNEXT_PT_water_flow, CLOTHNEXT_PT_water_flow_display

controls = set()
operators = set()
control_enabled = {}


class Layout:
    enabled = True

    def column(self, **kwargs):
        return self

    def row(self, **kwargs):
        return Layout()

    def box(self):
        return self

    def prop(self, owner, name, **kwargs):
        assert name in owner.bl_rna.properties, name
        controls.add(name)
        control_enabled[name] = self.enabled

    def label(self, **kwargs):
        pass

    def separator(self):
        pass

    def menu(self, identifier, **kwargs):
        assert identifier=='CLOTHNEXT_MT_water_source_provider'
        assert hasattr(bpy.types,identifier)

    def operator(self, identifier, **kwargs):
        assert identifier in {'cloth_next.prepare_water_flow','cloth_next.enable_flip_velocity_export'}
        assert getattr(bpy.ops.cloth_next,identifier.split('.')[1]).get_rna_type()
        operators.add(identifier)


cloth_next.register()
from cloth_next.blender import preferences
original_read = preferences._read_registry
preferences._read_registry = lambda: (SimpleNamespace(
    selected=SimpleNamespace(protocol_version='0.23')), None)
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
    obj.cloth_next.water_flow_resolution='CUSTOM'
    CLOTHNEXT_PT_water_flow.draw(SimpleNamespace(layout=Layout()), context)
    CLOTHNEXT_PT_water_flow_display.draw(SimpleNamespace(layout=Layout()), context)
    assert not getattr(bpy.types.CLOTHNEXT_PT_water_flow, 'bl_parent_id', '')
    assert obj.cloth_next.bl_rna.properties['water_flow_container'].name == 'GAIA Path'
    assert obj.cloth_next.bl_rna.properties['water_flow_container'].description == 'Where should the File be placed?'
    enum=obj.cloth_next.bl_rna.properties['water_flow_resolution'].enum_items
    assert {item.identifier:item.value for item in enum} == {'AUTO':0,'100':2,'150':3,'200':4,'CUSTOM':5}
    assert controls == {
        'water_flow_enabled', 'water_flow_domain', 'water_flow_influence',
        'water_flow_velocity_scale', 'water_flow_resolution', 'water_flow_container',
        'water_flow_custom_resolution',
        'water_flow_debug_mode', 'water_flow_show_bounds',
        'water_flow_show_vectors', 'water_flow_vector_stride', 'water_flow_vector_scale',
    }, controls
    assert operators == {'cloth_next.prepare_water_flow','cloth_next.enable_flip_velocity_export'}
    from cloth_next.blender import preferences
    try:
        preferences._read_registry = lambda: (SimpleNamespace(
            selected=SimpleNamespace(protocol_version='0.26',executable=None)), None)
        CLOTHNEXT_PT_water_flow.draw(SimpleNamespace(layout=Layout()), context)
        assert {'water_flow_coupling_strength', 'water_flow_native_flow'} <= controls
        assert 'water_hydrodynamic_pressure' in controls
        assert control_enabled['water_hydrodynamic_pressure'] is False
    finally:
        preferences._read_registry = original_read
    from cloth_next.blender.water_flow import CLOTHNEXT_MT_water_source_provider,CLOTHNEXT_OT_select_water_source
    providers=[]
    class ProviderRow:
        enabled=True
        def operator(self,identifier,**kwargs):
            assert identifier=='cloth_next.select_water_source'
            props=SimpleNamespace()
            providers.append((kwargs['text'],self.enabled,props))
            return props
    class ProviderLayout:
        def row(self):return ProviderRow()
    CLOTHNEXT_MT_water_source_provider.draw(SimpleNamespace(layout=ProviderLayout()),context)
    assert [(name,enabled) for name,enabled,_ in providers]==[
        ('FLIP Fluids',True),('Mantaflow',False),
        ('Houdini FX',False),('LiquiGen',False)]
    assert CLOTHNEXT_OT_select_water_source.description(context,SimpleNamespace(provider='MANTAFLOW'))=='Blenders Fluidsolver'
    assert CLOTHNEXT_OT_select_water_source.description(context,SimpleNamespace(provider='HOUDINI'))=='You should know how to export particle Data'
    assert 'Export velocity' in CLOTHNEXT_OT_select_water_source.description(context,SimpleNamespace(provider='LIQUIGEN'))
    assert bpy.ops.cloth_next.select_water_source(provider='FLIP_FLUIDS')=={'FINISHED'}
    print('WATER FLOW UI PASS: registered Physics panel, RNA controls, preparation and FLIP export operators')
finally:
    preferences._read_registry = original_read
    cloth_next.unregister()
assert not hasattr(bpy.types, 'CLOTHNEXT_PT_water_flow')
print('WATER FLOW UI PASS: registration cleanup')
