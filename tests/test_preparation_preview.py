from types import SimpleNamespace as NS
import importlib
import pytest


class Debug(dict):
    def __getattr__(self,key):return self.get(key,False)


def fixture(blender_env):
    m=importlib.import_module('cloth_next.blender.preparation_preview')
    debug=Debug(enable_fluid_particle_debug_output=True,export_force_field=True,
                export_internal_obstacle_mesh=True)
    obj=NS(flip_fluid=NS(object_type='TYPE_DOMAIN',domain=NS(debug=debug)))
    return m,debug,NS(objects=[obj])


def test_only_debug_drawing_masked_for_one_frame_and_restored(blender_env):
    m,debug,scene=fixture(blender_env)
    calls=[]
    def frame(frame,**kwargs):
        assert not debug.enable_fluid_particle_debug_output
        assert not debug.export_force_field
        assert debug.export_internal_obstacle_mesh
        calls.append((frame,kwargs))
    scene.frame_set=frame
    m.frame_set(scene,7,subframe=.5)
    assert calls==[(7,{'subframe':.5})]
    assert debug.enable_fluid_particle_debug_output and debug.export_force_field


def test_frame_exception_restores_flags(blender_env):
    m,debug,scene=fixture(blender_env)
    def fail(*args,**kwargs):raise RuntimeError('frame failed')
    scene.frame_set=fail
    with pytest.raises(RuntimeError,match='frame failed'):m.frame_set(scene,8)
    assert debug.enable_fluid_particle_debug_output and debug.export_force_field


def test_linked_read_only_domain_and_disabled_debug_are_untouched(blender_env):
    m,debug,scene=fixture(blender_env)
    debug['id_data']=NS(library=object(),override_library=None)
    observed=[]
    scene.frame_set=lambda frame:observed.append(debug.enable_fluid_particle_debug_output)
    m.frame_set(scene,1)
    assert observed==[True]


class Render(dict):
    """FLIP render settings: RNA enum values backed by ID property indices."""
    ITEMS = ('DISPLAY_FINAL', 'DISPLAY_PREVIEW', 'DISPLAY_NONE')

    def __init__(self, **values):
        super().__init__()
        self.rna = dict(values)
        enum = NS(enum_items=[NS(identifier=v) for v in self.ITEMS])
        self.bl_rna = NS(properties={k: enum for k in values})

    def __getattr__(self, key):
        if key in self:
            return self.ITEMS[self[key]]
        return self.rna[key]


def test_flip_display_meshes_are_not_loaded_during_preparation_frames(blender_env):
    m = importlib.import_module('cloth_next.blender.preparation_preview')
    render = Render(viewport_display='DISPLAY_FINAL', fluid_particle_viewport_display='DISPLAY_PREVIEW',
                    whitewater_viewport_display='DISPLAY_NONE')
    domain = NS(debug=Debug(), render=render)
    scene = NS(objects=[NS(flip_fluid=NS(object_type='TYPE_DOMAIN', domain=domain))])
    seen = []
    scene.frame_set = lambda frame: seen.append({k: getattr(render, k) for k in render.rna})
    m.frame_set(scene, 3)
    assert seen == [dict.fromkeys(render.rna, 'DISPLAY_NONE')]
    assert dict(render) == {} and render.viewport_display == 'DISPLAY_FINAL'
