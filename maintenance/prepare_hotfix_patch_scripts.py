from pathlib import Path


path = Path("maintenance/apply_animated_collider_cache_fix.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '    bend.deform_axis = "Z"\n',
    '    bend.deform_axis = "X"\n',
)

old_motion = '''replace_exact(
    solver_path,
    ''' + "'''" + '''                                "samples_per_frame": (int(getattr(
''' + "'''" + ''',
    ''' + "'''" + '''                                "animation_digest": (
                                    capture.content_digest
                                    if capture is not None else ""),
                                "samples_per_frame": (int(getattr(
''' + "'''" + ''',
    count=2)
'''
new_motion = '''replace_exact(
    solver_path,
    ''' + "'''" + '''                                "samples_per_frame": (int(getattr(
''' + "'''" + ''',
    ''' + "'''" + '''                                "animation_digest": (
                                    capture.content_digest
                                    if capture is not None else ""),
                                "samples_per_frame": (int(getattr(
''' + "'''" + ''')
replace_exact(
    solver_path,
    ''' + "'''" + '''                            "samples_per_frame": (int(getattr(
''' + "'''" + ''',
    ''' + "'''" + '''                            "animation_digest": (
                                capture.content_digest
                                if capture is not None else ""),
                            "samples_per_frame": (int(getattr(
''' + "'''" + ''')
'''
if old_motion not in text:
    raise RuntimeError("animated collider patch motion metadata anchor changed")
text = text.replace(old_motion, new_motion)

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
