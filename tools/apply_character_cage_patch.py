# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(
            f"Expected one patch anchor in {path}, found {text.count(old)}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "cloth_next/blender/object_properties.py",
    '''    collider_proxy_enabled: bpy.props.BoolProperty(
        name="Use Experimental Proxy", default=False,
        update=_on_settings_update,
        description="Replace this logical Collider with its generated "
                    "low-poly simulation Proxy during Bake")
    collider_proxy_target_vertices: bpy.props.IntProperty(
''',
    '''    collider_proxy_enabled: bpy.props.BoolProperty(
        name="Use Experimental Proxy", default=False,
        update=_on_settings_update,
        description="Replace this logical Collider with its generated "
                    "simulation Proxy during Bake")
    collider_proxy_type: bpy.props.EnumProperty(
        name="Proxy Type", default="SIMPLE", update=_on_settings_update,
        items=(("SIMPLE", "Simple Proxy",
                "Use the existing reduced deforming Mesh proxy"),
               ("CHARACTER_CAGE", "Character Collision Cage",
                "Build conservative rigid bone hulls from the animated Character")),
        description="Choose the generated Collider proxy strategy")
    collider_proxy_target_vertices: bpy.props.IntProperty(
''')
replace_once(
    "cloth_next/blender/object_properties.py",
    '''    collider_proxy_result_vertices: bpy.props.IntProperty(
        name="Proxy Result Vertices", default=0, options={"HIDDEN"})
    material: bpy.props.PointerProperty(type=CLOTHNEXT_PG_material_settings)
''',
    '''    collider_proxy_result_vertices: bpy.props.IntProperty(
        name="Proxy Result Vertices", default=0, options={"HIDDEN"})
    collider_cage_margin: bpy.props.FloatProperty(
        name="Cage Margin", default=0.003, min=0.0, soft_max=0.02,
        precision=4, update=_on_settings_update,
        description="Outward safety margin added to every bone hull in world units")
    collider_cage_joint_overlap: bpy.props.FloatProperty(
        name="Joint Overlap", default=0.01, min=0.0, soft_max=0.05,
        precision=4, update=_on_settings_update,
        description="Extend bone hulls along the bone axis so joints overlap")
    collider_cage_sample_step: bpy.props.IntProperty(
        name="Animation Sample Step", default=1, min=1, max=32,
        update=_on_settings_update,
        description="Frames between one-time Character evaluations while fitting the cage")
    collider_cage_weight_threshold: bpy.props.FloatProperty(
        name="Bone Weight Threshold", default=0.2, min=0.0, max=1.0,
        precision=2, update=_on_settings_update,
        description="Minimum skin weight for a Character vertex to contribute to a bone hull")
    collider_cage_min_vertices: bpy.props.IntProperty(
        name="Minimum Bone Vertices", default=24, min=4, max=10000,
        update=_on_settings_update,
        description="Ignore deform bones with fewer weighted Character vertices")
    material: bpy.props.PointerProperty(type=CLOTHNEXT_PG_material_settings)
''')
replace_once(
    "cloth_next/blender/object_properties.py",
    '''    settings.collider_proxy_enabled = False
    owner = getattr(settings, "id_data", None)
''',
    '''    settings.collider_proxy_enabled = False
    settings.collider_proxy_type = "SIMPLE"
    settings.collider_cage_margin = 0.003
    settings.collider_cage_joint_overlap = 0.01
    settings.collider_cage_sample_step = 1
    settings.collider_cage_weight_threshold = 0.2
    settings.collider_cage_min_vertices = 24
    owner = getattr(settings, "id_data", None)
''')

replace_once(
    "cloth_next/blender/physics_ui.py",
    '''                if not collider_proxy.is_generated_proxy(context.object):
                    proxy_box = layout.box()
                    proxy_box.label(text="Simulation Proxy · Preview",
                                    icon="ERROR")
                    proxy_box.prop(settings, "collider_proxy_target_vertices")
                    proxy = getattr(settings, "collider_proxy_object", None)
                    action = proxy_box.row(align=True)
                    action.operator(
                        "clothnext.generate_collider_proxy",
                        text="Regenerate Proxy" if proxy else "Generate Proxy")
                    if proxy:
                        proxy_box.prop(settings, "collider_proxy_enabled")
                        estimate = collider_proxy.proxy_estimate(
                            context.object, proxy)
                        proxy_box.label(
                            text=(f"Geometry: {estimate.source_vertices:,} → "
                                  f"{estimate.proxy_vertices:,} vertices"))
                        proxy_box.label(
                            text=(f"Estimated PPF peak: "
                                  f"{collider_proxy.format_bytes(estimate.source_peak_bytes)} "
                                  f"→ {collider_proxy.format_bytes(estimate.proxy_peak_bytes)}"),
                            icon="MEMORY")
                        proxy_box.label(
                            text="Regenerate after topology or deformer changes",
                            icon="INFO")
                    else:
                        proxy_box.label(
                            text="Original Collider remains active until generated",
                            icon="INFO")
''',
    '''                if not collider_proxy.is_proxy_implementation_object(context.object):
                    proxy_box = layout.box()
                    proxy_box.label(text="Simulation Proxy · Preview",
                                    icon="ERROR")
                    proxy_box.prop(settings, "collider_proxy_type")
                    cage_mode = settings.collider_proxy_type == "CHARACTER_CAGE"
                    if cage_mode:
                        proxy_box.prop(settings, "collider_cage_margin")
                        proxy_box.prop(settings, "collider_cage_joint_overlap")
                        proxy_box.prop(settings, "collider_cage_sample_step")
                        proxy_box.prop(settings, "collider_cage_weight_threshold")
                        proxy_box.prop(settings, "collider_cage_min_vertices")
                    else:
                        proxy_box.prop(settings, "collider_proxy_target_vertices")
                    proxy = getattr(settings, "collider_proxy_object", None)
                    action = proxy_box.row(align=True)
                    action.operator(
                        "clothnext.generate_collider_proxy",
                        text=("Regenerate Character Cage" if proxy and cage_mode else
                              "Generate Character Cage" if cage_mode else
                              "Regenerate Simple Proxy" if proxy else
                              "Generate Simple Proxy"))
                    if proxy:
                        proxy_box.prop(settings, "collider_proxy_enabled")
                        estimate = collider_proxy.proxy_estimate(
                            context.object, proxy)
                        if cage_mode:
                            segment_count = (collider_proxy.character_collision_cage.
                                             cage_segment_count(context.object))
                            proxy_box.label(
                                text=(f"Cage: {segment_count} bone hulls · "
                                      f"{estimate.proxy_vertices:,} vertices"))
                            proxy_box.label(
                                text=("Mesh deformation is captured once while fitting; "
                                      "Bake uses transform-only rigid hulls"),
                                icon="ARMATURE_DATA")
                        else:
                            proxy_box.label(
                                text=(f"Geometry: {estimate.source_vertices:,} → "
                                      f"{estimate.proxy_vertices:,} vertices"))
                        proxy_box.label(
                            text=(f"Estimated PPF peak: "
                                  f"{collider_proxy.format_bytes(estimate.source_peak_bytes)} "
                                  f"→ {collider_proxy.format_bytes(estimate.proxy_peak_bytes)}"),
                            icon="MEMORY")
                        proxy_box.label(
                            text="Regenerate after topology, rig, or animation changes",
                            icon="INFO")
                    else:
                        proxy_box.label(
                            text="Original Collider remains active until generated",
                            icon="INFO")
''')

Path("tests/test_character_collision_cage.py").write_text(
    '''# SPDX-License-Identifier: GPL-3.0-or-later

import pytest


def test_character_cage_frame_sampling_includes_end(blender_env):
    cage = blender_env.collider_proxy.character_collision_cage
    assert cage.sample_frames(1, 10, 4) == (1, 5, 9, 10)
    assert cage.sample_frames(3, 3, 8) == (3,)


def test_character_cage_rejects_inverted_range(blender_env):
    cage = blender_env.collider_proxy.character_collision_cage
    with pytest.raises(cage.CharacterCageError, match="must not precede"):
        cage.sample_frames(10, 1, 1)


def test_character_cage_properties_are_registered(blender_env):
    env = blender_env
    env.registration.register()
    obj = env.bpy.types.Object(name="Body", type="MESH")
    settings = obj.cloth_next
    assert settings.collider_proxy_type == "SIMPLE"
    assert settings.collider_cage_margin == pytest.approx(0.003)
    assert settings.collider_cage_joint_overlap == pytest.approx(0.01)
    assert settings.collider_cage_sample_step == 1
    assert settings.collider_cage_weight_threshold == pytest.approx(0.2)
    assert settings.collider_cage_min_vertices == 24
    env.registration.unregister()
''',
    encoding="utf-8")

print("Character Collision Cage patch applied")
