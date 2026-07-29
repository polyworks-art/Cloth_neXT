from pathlib import Path


# ---------------------------------------------------------------------------
# Animated Collider cache patch preparation

path = Path("maintenance/apply_animated_collider_cache_fix.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '    bend.deform_axis = "Z"\n',
    '    bend.deform_axis = "X"\n',
)

# Replace the original broad line-level patch with two complete, uniquely
# identifiable motion_meta blocks. Their indentation differs, and matching the
# shorter indentation alone also matches inside the longer one.
start_marker = """replace_exact(
    solver_path,
    '''                                \"samples_per_frame\": (int(getattr(
"""
end_marker = """
replace_exact(
    solver_path,
    '''        \"export_schema\": 2,
"""
start = text.index(start_marker)
end = text.index(end_marker, start)
motion_patch = """replace_exact(
    solver_path,
    '''            motion_meta.append({\"name\": obj.name, \"motion_type\": motion_type,
                                \"samples_per_frame\": (int(getattr(
                                    obj.cloth_next,
                                    \"collider_samples_per_frame\",
                                    COLLIDER_SAMPLES_PER_FRAME))
                                    if capture is not None else 0),
                                \"vertex_count\": len(vertices),
                                \"triangle_count\": len(triangles)})
''',
    '''            motion_meta.append({\"name\": obj.name, \"motion_type\": motion_type,
                                \"animation_digest\": (
                                    capture.content_digest
                                    if capture is not None else \"\"),
                                \"samples_per_frame\": (int(getattr(
                                    obj.cloth_next,
                                    \"collider_samples_per_frame\",
                                    COLLIDER_SAMPLES_PER_FRAME))
                                    if capture is not None else 0),
                                \"vertex_count\": len(vertices),
                                \"triangle_count\": len(triangles)})
''')
replace_exact(
    solver_path,
    '''        motion_meta.append({\"name\": current.name, \"uuid\": collider_uuid,
                            \"motion_type\": motion_type,
                            \"samples_per_frame\": (int(getattr(
                                current.cloth_next,
                                \"collider_samples_per_frame\",
                                COLLIDER_SAMPLES_PER_FRAME))
                                if capture is not None else 0),
                            \"vertex_count\": len(vertices),
                            \"triangle_count\": len(triangles)})
''',
    '''        motion_meta.append({\"name\": current.name, \"uuid\": collider_uuid,
                            \"motion_type\": motion_type,
                            \"animation_digest\": (
                                capture.content_digest
                                if capture is not None else \"\"),
                            \"samples_per_frame\": (int(getattr(
                                current.cloth_next,
                                \"collider_samples_per_frame\",
                                COLLIDER_SAMPLES_PER_FRAME))
                                if capture is not None else 0),
                            \"vertex_count\": len(vertices),
                            \"triangle_count\": len(triangles)})
''')
"""
text = text[:start] + motion_patch + text[end:]

old_artifact = '''artifact_path = "tests/test_built_artifacts.py"
replace_exact(
    artifact_path,
    ''' + "'''" + '''    assert "SCENE_EXPORT_CACHE_SCHEMA" in solver_test
''' + "'''" + ''',
    ''' + "'''" + '''    assert "SCENE_EXPORT_CACHE_SCHEMA" in solver_test
    assert '\"animation_digest\"' in solver_test
    assert "content_digest=motion_hasher.hexdigest()" in solver_test
''' + "'''" + ''')
'''
new_artifact = '''artifact_path = "tests/test_built_artifacts.py"
replace_exact(
    artifact_path,
    ''' + "'''" + '''    assert '\"_sample_frame_offset\": frame_offsets' in solver_export
''' + "'''" + ''',
    ''' + "'''" + '''    assert '\"_sample_frame_offset\": frame_offsets' in solver_export
    assert "SCENE_EXPORT_CACHE_SCHEMA = 3" in solver_export
    assert '\"animation_digest\"' in solver_export
    assert "content_digest=motion_hasher.hexdigest()" in solver_export
''' + "'''" + ''')
'''
if old_artifact not in text:
    raise RuntimeError("animated collider patch artifact anchor changed")
text = text.replace(old_artifact, new_artifact)
path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Character Cage UI patch preparation

ui_patch_path = Path("maintenance/apply_character_cage_proxy_ui_fix.py")
ui_patch = ui_patch_path.read_text(encoding="utf-8")
ui_patch = ui_patch.replace(
    '''        simple_proxy = bool(
            proxy is not None and collider_proxy.is_generated_proxy(proxy) and
            not collider_proxy.character_collision_cage.is_cage_segment(proxy))
''',
    '''        simple_proxy = bool(
            proxy is not None and
            not collider_proxy.character_collision_cage.is_cage_segment(proxy))
''')

old_estimate = '''        estimate = collider_proxy.proxy_estimate(context.object, proxy)
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
new_estimate = '''        # Panel.draw must never inspect proxy geometry. Generation stores these
        # counts on the source; use only those cached values during redraw.
        source_vertices = int(
            getattr(settings, "collider_proxy_source_vertices", 0) or 0)
        proxy_vertices = int(
            getattr(settings, "collider_proxy_result_vertices", 0) or 0)
        frame_settings = _authoritative_frame_settings(context)
        samples = collider_proxy.motion_sample_count(
            int(frame_settings.bake_start), int(frame_settings.bake_end),
            int(settings.collider_samples_per_frame))
        proxy_samples = 1 if cage_mode else samples
        source_peak = collider_proxy.estimated_ppf_peak_bytes(
            source_vertices, samples)
        proxy_peak = collider_proxy.estimated_ppf_peak_bytes(
            proxy_vertices, proxy_samples)
        if cage_mode:
            segment_count = (collider_proxy.character_collision_cage.
                             cage_segment_count(context.object))
            layout.label(
                text=(f"Cage: {segment_count} bone hulls · "
                      f"{proxy_vertices:,} vertices"))
            layout.label(
                text="Character sampled once; Bake uses rigid bone hulls",
                icon="ARMATURE_DATA")
        else:
            layout.label(text=f"Source: {source_vertices:,} vertices")
            layout.label(text=f"Proxy: {proxy_vertices:,} vertices")
        layout.label(text="Estimated Peak Memory")
        layout.label(
            text=(f"{collider_proxy.format_bytes(source_peak)} → "
                  f"{collider_proxy.format_bytes(proxy_peak)}"),
            icon="MEMORY")
        layout.label(
            text="Regenerate after topology, rig, or animation changes",
            icon="INFO")
'''
if old_estimate not in ui_patch:
    raise RuntimeError("proxy estimate UI block changed")
ui_patch = ui_patch.replace(old_estimate, new_estimate)

existing_test_update = '''replace_once(
    test_path,
    ''' + "'''" + '''    assert panel.layout.props == [
        "collider_proxy_target_vertices", "collider_proxy_enabled"]
    assert panel.layout.operators == [
        ("clothnext.generate_collider_proxy", "Regenerate Proxy")]
''' + "'''" + ''',
    ''' + "'''" + '''    assert panel.layout.props == [
        "collider_proxy_type", "collider_proxy_target_vertices",
        "collider_proxy_enabled"]
    assert panel.layout.operators == [
        ("clothnext.generate_collider_proxy", "Regenerate Simple Proxy")]
''' + "'''" + ''')

'''
self_delete = 'Path("maintenance/apply_character_cage_proxy_ui_fix.py").unlink()\n'
if self_delete not in ui_patch:
    raise RuntimeError("proxy UI patch self-delete anchor changed")
ui_patch = ui_patch.replace(self_delete, existing_test_update + self_delete)
ui_patch_path.write_text(ui_patch, encoding="utf-8")

Path("maintenance/prepare_hotfix_patch_scripts.py").unlink()
