from types import SimpleNamespace as NS
import importlib


def setup(blender_env):
    m = importlib.import_module('cloth_next.blender.water_settings')
    scene = NS(objects=[], cloth_next_water_flow_source=None, cloth_next_water_flow_shared=False)
    other_scene = NS(objects=[], cloth_next_water_flow_source=None, cloth_next_water_flow_shared=False)
    def obj(name, scene=scene, role='CLOTH', enabled=True):
        item = NS(name=name, users_scene=(scene,))
        item.cloth_next = NS(id_data=item, enabled=enabled, role=role,
            **{key: False if key == 'water_flow_enabled' else None if key == 'water_flow_domain'
               else '' if key == 'water_flow_container' else 1 for key in m.SHARED_PROPERTIES},
            water_flow_influence=.25, water_flow_velocity_scale=.8)
        scene.objects.append(item)
        return item
    a, b = obj('A'), obj('B')
    return m, scene, other_scene, obj, a, b


def test_shares_enable_setup_and_disable_without_copying_object_controls(blender_env):
    m, scene, _, obj, a, b = setup(blender_env)
    collider = obj('Collider', role='COLLIDER')
    inactive = obj('Inactive', enabled=False)
    b.cloth_next.water_flow_influence=.9
    b.cloth_next.water_flow_velocity_scale=1.3
    a.cloth_next.water_flow_enabled=True
    a.cloth_next.water_flow_domain=NS(name='Domain')
    a.cloth_next.water_flow_container='/shared/Flow.gaia'
    a.cloth_next.water_flow_coupling_strength=3
    m.share_from(a.cloth_next, NS(scene=scene))
    assert all(getattr(a.cloth_next,k)==getattr(b.cloth_next,k) for k in m.SHARED_PROPERTIES)
    assert b.cloth_next.water_flow_influence==.9 and b.cloth_next.water_flow_velocity_scale==1.3
    assert not collider.cloth_next.water_flow_enabled and not inactive.cloth_next.water_flow_enabled
    b.cloth_next.water_flow_enabled=False
    m.share_from(b.cloth_next, NS(scene=scene))
    assert not a.cloth_next.water_flow_enabled
    assert scene.cloth_next_water_flow_source is b


def test_new_cloth_inherits_disabled_flow_from_disabled_source(blender_env):
    m, scene, _, obj, a, _ = setup(blender_env)
    a.cloth_next.water_flow_container='/configured.gaia'
    m.share_from(a.cloth_next, NS(scene=scene))
    a.cloth_next.enabled=False
    newcomer=obj('New')
    m.inherit(newcomer.cloth_next, NS(scene=scene))
    assert not newcomer.cloth_next.water_flow_enabled
    assert newcomer.cloth_next.water_flow_container=='/configured.gaia'


def test_scene_isolation_and_fallback_to_actual_owner_scene(blender_env):
    m, scene, other, obj, a, b = setup(blender_env)
    separate=obj('Other', scene=other)
    a.cloth_next.water_flow_enabled=True
    m.share_from(a.cloth_next, NS(scene=other))
    assert b.cloth_next.water_flow_enabled
    assert not separate.cloth_next.water_flow_enabled


def test_load_migrates_legacy_enabled_flow_and_keeps_object_controls(blender_env):
    m, scene, _, _, a, b = setup(blender_env)
    a.cloth_next.water_flow_enabled=True
    a.cloth_next.water_flow_container='/legacy.gaia'
    b.cloth_next.water_flow_influence=.7
    blender_env.bpy.data.scenes=[scene]
    m._load_post()
    assert b.cloth_next.water_flow_enabled and b.cloth_next.water_flow_container=='/legacy.gaia'
    assert b.cloth_next.water_flow_influence==.7


def test_deleted_source_preserves_disabled_shared_setup_on_remaining_cloth(blender_env):
    m, scene, _, obj, a, b = setup(blender_env)
    a.cloth_next.water_flow_container='/kept.gaia'
    m.share_from(a.cloth_next, NS(scene=scene))
    scene.objects.remove(a)
    scene.cloth_next_water_flow_source=None
    newcomer=obj('New')
    m._depsgraph_post(scene, None)
    assert newcomer.cloth_next.water_flow_container=='/kept.gaia'
    assert not newcomer.cloth_next.water_flow_enabled
    assert scene.cloth_next_water_flow_source is b
