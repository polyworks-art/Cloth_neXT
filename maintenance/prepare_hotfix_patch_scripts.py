from pathlib import Path


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
Path("maintenance/prepare_hotfix_patch_scripts.py").unlink()
