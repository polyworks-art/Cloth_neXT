from pathlib import Path


path = Path("maintenance/apply_animated_collider_cache_fix.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '    bend.deform_axis = "Z"\n',
    '    bend.deform_axis = "X"\n',
)
old = '''artifact_path = "tests/test_built_artifacts.py"
replace_exact(
    artifact_path,
    ''' + "'''" + '''    assert "SCENE_EXPORT_CACHE_SCHEMA" in solver_test
''' + "'''" + ''',
    ''' + "'''" + '''    assert "SCENE_EXPORT_CACHE_SCHEMA" in solver_test
    assert '\"animation_digest\"' in solver_test
    assert "content_digest=motion_hasher.hexdigest()" in solver_test
''' + "'''" + ''')
'''
new = '''artifact_path = "tests/test_built_artifacts.py"
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
if old not in text:
    raise RuntimeError("animated collider patch artifact anchor changed")
path.write_text(text.replace(old, new), encoding="utf-8")
Path("maintenance/prepare_hotfix_patch_scripts.py").unlink()
