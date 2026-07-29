from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    file.write_text(text.replace(old, new), encoding="utf-8")


ui_path = "cloth_next/blender/physics_ui.py"
ui = Path(ui_path).read_text(encoding="utf-8")
start = ui.index("class CLOTHNEXT_PT_simulation_proxy")
end = ui.index("\n\nclass CLOTHNEXT_PT_force", start)
replacement = '''class CLOTHNEXT_PT_simulation_proxy(_ClothNextSubpanel, bpy.types.Panel):
    bl_label = "Simulation Proxy"
    bl_idname = "CLOTHNEXT_PT_simulation_proxy"
    bl_parent_id = "CLOTHNEXT_PT_collider_collision"
    bl_options = {"DEFAULT_CLOSED"}
    roles = {"COLLIDER"}
    header_icon = "simulation_proxy"

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False
        settings = context.object.cloth_next
        return (settings.collider_motion == "ANIMATED"
                and not collider_proxy.is_generated_proxy(context.object))

    def draw(self, context):
        settings = context.object.cloth_next
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(settings, "collider_proxy_type")
        cage_mode = settings.collider_proxy_type == "CHARACTER_CAGE"
        if cage_mode:
            cage = layout.column(align=True)
            cage.prop(settings, "collider_cage_margin")
            cage.prop(settings, "collider_cage_joint_overlap")
            cage.prop(settings, "collider_cage_sample_step")
            cage.prop(settings, "collider_cage_weight_threshold")
            cage.prop(settings, "collider_cage_min_vertices")
        else:
            layout.prop(settings, "collider_proxy_target_vertices",
                        text="Target Vertices")

        proxy = getattr(settings, "collider_proxy_object", None)
        cage_proxy = bool(
            proxy is not None and
            collider_proxy.character_collision_cage.is_primary_cage_segment(proxy))
        simple_proxy = bool(
            proxy is not None and collider_proxy.is_generated_proxy(proxy) and
            not collider_proxy.character_collision_cage.is_cage_segment(proxy))
        proxy_matches_mode = cage_proxy if cage_mode else simple_proxy

        action = layout.row()
        action.enabled = not shared_controller.snapshot().active
        action.operator(
            "clothnext.generate_collider_proxy",
            text=("Regenerate Character Cage" if proxy_matches_mode and cage_mode
                  else "Generate Character Cage" if cage_mode
                  else "Regenerate Simple Proxy" if proxy_matches_mode
                  else "Generate Simple Proxy"))

        if not proxy_matches_mode:
            if bool(settings.collider_proxy_enabled):
                layout.prop(settings, "collider_proxy_enabled",
                            text="Use Simulation Proxy")
                layout.label(
                    text="Selected Proxy Type is missing; generate it or disable Proxy",
                    icon="ERROR")
            elif proxy is not None:
                layout.label(
                    text="Generate the selected Proxy Type before enabling it",
                    icon="INFO")
            else:
                layout.label(
                    text="Original Collider remains active until generated",
                    icon="INFO")
            return

        layout.prop(settings, "collider_proxy_enabled",
                    text="Use Simulation Proxy")
        estimate = collider_proxy.proxy_estimate(context.object, proxy)
        if cage_mode:
            segment_count = (collider_proxy.character_collision_cage.
                             cage_segment_count(context.object))
            layout.label(
                text=(f"Cage: {segment_count} bone hulls · "
                      f"{estimate.proxy_vertices:,} vertices"))
            layout.label(
                text="Character sampled once; Bake uses rigid bone hulls",
                icon="ARMATURE_DATA")
        else:
            layout.label(
                text=(f"Geometry: {estimate.source_vertices:,} → "
                      f"{estimate.proxy_vertices:,} vertices"))
        layout.label(
            text=(f"Estimated PPF peak: "
                  f"{collider_proxy.format_bytes(estimate.source_peak_bytes)} → "
                  f"{collider_proxy.format_bytes(estimate.proxy_peak_bytes)}"),
            icon="MEMORY")
        layout.label(
            text="Regenerate after topology, rig, or animation changes",
            icon="INFO")
'''
Path(ui_path).write_text(ui[:start] + replacement + ui[end:], encoding="utf-8")

props_path = "cloth_next/blender/object_properties.py"
callback_anchor = '''def _on_settings_update(self, _context) -> None:
    """Solver-visible value changed: record DIRTY, compute nothing."""
    _mark_dirty(self)
'''
callback_replacement = callback_anchor + '''

def _on_collider_proxy_type_update(self, _context) -> None:
    """Changing strategy invalidates the generated proxy from the other mode."""
    self.collider_proxy_enabled = False
    _mark_dirty(self)
'''
replace_once(props_path, callback_anchor, callback_replacement)
replace_once(
    props_path,
    '    collider_proxy_type: bpy.props.EnumProperty(\n'
    '        name="Proxy Type", default="SIMPLE", update=_on_settings_update,\n',
    '    collider_proxy_type: bpy.props.EnumProperty(\n'
    '        name="Proxy Type", default="SIMPLE",\n'
    '        update=_on_collider_proxy_type_update,\n')

test_path = "tests/test_phase3b_material_ui.py"
test_anchor = '''def test_material_panel_displays_artist_facing_names(blender_env):
'''
test_block = '''def test_active_simulation_proxy_panel_exposes_simple_and_character_modes(
        blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "COLLIDER"
    settings.collider_motion = "ANIMATED"
    context = SimpleNamespace(
        object=obj, active_object=obj,
        scene=SimpleNamespace(objects=[obj]))

    settings.collider_proxy_type = "SIMPLE"
    panel = env.physics_ui.CLOTHNEXT_PT_simulation_proxy()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert panel.layout.props == [
        "collider_proxy_type", "collider_proxy_target_vertices"]
    assert ("clothnext.generate_collider_proxy", "Generate Simple Proxy") \
        in panel.layout.operators

    settings.collider_proxy_type = "CHARACTER_CAGE"
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert panel.layout.props == [
        "collider_proxy_type", "collider_cage_margin",
        "collider_cage_joint_overlap", "collider_cage_sample_step",
        "collider_cage_weight_threshold", "collider_cage_min_vertices"]
    assert "collider_proxy_target_vertices" not in panel.layout.props
    assert ("clothnext.generate_collider_proxy", "Generate Character Cage") \
        in panel.layout.operators
    env.registration.unregister()


def test_proxy_type_change_disables_the_other_generated_mode(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    settings.collider_proxy_enabled = True
    env.object_properties._on_collider_proxy_type_update(settings, None)
    assert settings.collider_proxy_enabled is False
    env.registration.unregister()


''' + test_anchor
replace_once(test_path, test_anchor, test_block)

artifact_path = "tests/test_built_artifacts.py"
replace_once(
    artifact_path,
    '    assert "Simulation Proxy" in physics_ui\n',
    '    assert "Simulation Proxy" in physics_ui\n'
    '    assert \'layout.prop(settings, "collider_proxy_type")\' in physics_ui\n'
    '    assert "Generate Character Cage" in physics_ui\n')

Path("maintenance/apply_character_cage_proxy_ui_fix.py").unlink()
