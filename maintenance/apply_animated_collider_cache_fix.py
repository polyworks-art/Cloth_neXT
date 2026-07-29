from pathlib import Path


def replace_exact(path: str, old: str, new: str, *, count: int = 1) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(
            f"{path}: expected {count} occurrence(s), found {actual}: {old[:100]!r}")
    file.write_text(text.replace(old, new), encoding="utf-8")


solver_path = "cloth_next/blender/solver_test.py"

replace_exact(
    solver_path,
    '''    animation: dict | None = None
    temporary_path: Path | None = None
''',
    '''    animation: dict | None = None
    temporary_path: Path | None = None
    # Digest of the exact sampled motion that produced ``animation``. It is
    # part of the persistent Scene payload identity so an old Frame-1 collider
    # can never be restored after animation or exporter changes.
    content_digest: str = ""
''')

matrix_anchor = '''def _matrix_trs(matrix):
    """Decompose one solver-space matrix using Blender's evaluated math."""
    from mathutils import Matrix
    location, rotation, scale = Matrix(matrix).decompose()
    return ([float(value) for value in location],
            [float(rotation.w), float(rotation.x), float(rotation.y),
             float(rotation.z)],
            [float(value) for value in scale])


COLLIDER_SAMPLES_PER_FRAME = 8
'''
matrix_replacement = '''def _matrix_trs(matrix):
    """Decompose one solver-space matrix using Blender's evaluated math."""
    from mathutils import Matrix
    location, rotation, scale = Matrix(matrix).decompose()
    return ([float(value) for value in location],
            [float(rotation.w), float(rotation.x), float(rotation.y),
             float(rotation.z)],
            [float(value) for value in scale])


def _collider_motion_digest(frame_offsets, samples, *, dtype="<f8") -> str:
    """Hash the exact sampled motion without materializing a second full copy."""
    digest = hashlib.sha256()
    offsets = np.ascontiguousarray(frame_offsets, dtype="<f8")
    digest.update(memoryview(offsets).cast("B"))
    values = np.asarray(samples, dtype=dtype)
    if not values.flags.c_contiguous:
        values = np.ascontiguousarray(values)
    digest.update(memoryview(values).cast("B"))
    return digest.hexdigest()


COLLIDER_SAMPLES_PER_FRAME = 8
SCENE_EXPORT_CACHE_SCHEMA = 3
'''
replace_exact(solver_path, matrix_anchor, matrix_replacement)

transform_return = '''        return ColliderMotionCapture(
            "RIGID_ANIMATED", vertices, triangles, matrices[0],
            {"time": times, "_sample_frame_offset": frame_offsets, "translation": translations,
             "quaternion": quaternions, "scale": scales,
             "segments": [
                 {"interpolation": "LINEAR",
                  "handle_right": [1.0 / 3.0, 0.0],
                  "handle_left": [2.0 / 3.0, 1.0]}
                 for _index in range(len(sample_points) - 1)]})
'''
transform_return_new = '''        return ColliderMotionCapture(
            "RIGID_ANIMATED", vertices, triangles, matrices[0],
            {"time": times, "_sample_frame_offset": frame_offsets, "translation": translations,
             "quaternion": quaternions, "scale": scales,
             "segments": [
                 {"interpolation": "LINEAR",
                  "handle_right": [1.0 / 3.0, 0.0],
                  "handle_left": [2.0 / 3.0, 1.0]}
                 for _index in range(len(sample_points) - 1)]},
            content_digest=_collider_motion_digest(frame_offsets, matrices))
'''
replace_exact(solver_path, transform_return, transform_return_new)

replace_exact(
    solver_path,
    '''    temporary_path = None
    deforming = False
    topology_check_mode = _collider_topology_check_mode(collider_obj)
''',
    '''    temporary_path = None
    deforming = False
    motion_hasher = hashlib.sha256()
    topology_check_mode = _collider_topology_check_mode(collider_obj)
''')

sample_store = '''                # Store solver-world positions immediately.  This replaces
                # the former second pass over every frame and every vertex.
                transform = np.asarray(solver_matrix, dtype=np.float64)
                local_samples[offset] = (
                    local @ transform[:3, :3].T + transform[:3, 3])
'''
sample_store_new = '''                # Store solver-world positions immediately.  This replaces
                # the former second pass over every frame and every vertex.
                transform = np.asarray(solver_matrix, dtype=np.float64)
                world_sample = np.asarray(
                    local @ transform[:3, :3].T + transform[:3, 3],
                    dtype="<f4")
                local_samples[offset] = world_sample
                motion_hasher.update(struct.pack("<d", float(frame_offsets[offset])))
                motion_hasher.update(memoryview(world_sample).cast("B"))
'''
replace_exact(solver_path, sample_store, sample_store_new)

rigid_result = '''            result = ColliderMotionCapture(
                "RIGID_ANIMATED",
                tuple(tuple(float(value) for value in row)
                      for row in reference_vertices),
                reference_triangles, matrices[0],
                {"time": times, "_sample_frame_offset": frame_offsets, "translation": translations,
                 "quaternion": quaternions, "scale": scales,
                 "segments": [
                     {"interpolation": "LINEAR",
                      "handle_right": [1.0 / 3.0, 0.0],
                      "handle_left": [2.0 / 3.0, 1.0]}
                     for _index in range(sample_count - 1)]})
'''
rigid_result_new = '''            result = ColliderMotionCapture(
                "RIGID_ANIMATED",
                tuple(tuple(float(value) for value in row)
                      for row in reference_vertices),
                reference_triangles, matrices[0],
                {"time": times, "_sample_frame_offset": frame_offsets, "translation": translations,
                 "quaternion": quaternions, "scale": scales,
                 "segments": [
                     {"interpolation": "LINEAR",
                      "handle_right": [1.0 / 3.0, 0.0],
                      "handle_left": [2.0 / 3.0, 1.0]}
                     for _index in range(sample_count - 1)]},
                content_digest=motion_hasher.hexdigest())
'''
replace_exact(solver_path, rigid_result, rigid_result_new)

replace_exact(
    solver_path,
    '''        return ColliderMotionCapture(
            "DEFORMING_ANIMATED",
            tuple(tuple(float(value) for value in row)
                  for row in local_samples[0]),
            reference_triangles, identity,
            {"time": times, "_sample_frame_offset": frame_offsets, "vert_frames": local_samples}, temporary_path)
''',
    '''        return ColliderMotionCapture(
            "DEFORMING_ANIMATED",
            tuple(tuple(float(value) for value in row)
                  for row in local_samples[0]),
            reference_triangles, identity,
            {"time": times, "_sample_frame_offset": frame_offsets, "vert_frames": local_samples},
            temporary_path, content_digest=motion_hasher.hexdigest())
''')

replace_exact(
    solver_path,
    '''                                "samples_per_frame": (int(getattr(
''',
    '''                                "animation_digest": (
                                    capture.content_digest
                                    if capture is not None else ""),
                                "samples_per_frame": (int(getattr(
''',
    count=2)

replace_exact(
    solver_path,
    '''        "export_schema": 2,
''',
    '''        # v3 invalidates plans written before animated Collider frame
        # digests participated in the Scene payload identity.
        "export_schema": SCENE_EXPORT_CACHE_SCHEMA,
''')

# Pure regression coverage: every axis and every sample time must affect the
# digest, and the cache schema bump must remain explicit.
test_path = "tests/test_solver_test_ui.py"
test_anchor = '''def test_shell_uv_export_preserves_authored_uvs_and_generates_fallback(
'''
test_block = '''def test_animated_collider_motion_digest_covers_all_axes_and_times(blender_env):
    module = blender_env.solver_test
    offsets = (0.0, 1.0)
    base = np.zeros((2, 3, 3), dtype=np.float32)
    baseline = module._collider_motion_digest(offsets, base, dtype="<f4")
    assert baseline == module._collider_motion_digest(offsets, base.copy(), dtype="<f4")

    for axis in range(3):
        moved = base.copy()
        moved[1, :, axis] = float(axis + 1)
        assert module._collider_motion_digest(offsets, moved, dtype="<f4") != baseline

    assert module._collider_motion_digest((0.0, 2.0), base, dtype="<f4") != baseline
    assert module.SCENE_EXPORT_CACHE_SCHEMA == 3


''' + test_anchor
replace_exact(test_path, test_anchor, test_block)

# Real Blender capture: changing a deforming animation must produce a new
# digest, which is the value threaded into the persistent Scene cache key.
smoke_path = "tools/blender_collider_proxy_smoke.py"
replace_exact(smoke_path, "import bpy\n", "import bpy\nimport numpy as np\n")
replace_exact(
    smoke_path,
    '''from cloth_next.blender import collider_proxy, registration  # noqa: E402
''',
    '''from cloth_next.blender import collider_proxy, registration, solver_test  # noqa: E402
from cloth_next.bake.frame_range import BakeFrameRange  # noqa: E402
''')

smoke_anchor = '''def _build_character():
'''
smoke_block = '''def _animated_collider_cache_identity_smoke(scene):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8,
                                    location=(-4.0, 0.0, 0.0))
    source = bpy.context.object
    source.name = "AnimatedColliderDigestSmoke"
    source.cloth_next.enabled = True
    source.cloth_next.role = "COLLIDER"
    source.cloth_next.collider_motion = "ANIMATED"
    source.cloth_next.collider_capture_mode = "DEFORMING"
    source.cloth_next.collider_samples_per_frame = 2
    bend = source.modifiers.new("Digest Bend", "SIMPLE_DEFORM")
    bend.deform_method = "BEND"
    bend.deform_axis = "Z"
    bend.angle = 0.0
    bend.keyframe_insert("angle", frame=1)
    bend.angle = 0.35
    bend.keyframe_insert("angle", frame=3)

    bake_range = BakeFrameRange(1, 3)
    first = solver_test._capture_collider_motion(bpy.context, source, bake_range)
    try:
        assert first.motion_type == "DEFORMING_ANIMATED"
        first_digest = first.content_digest
        first_last = np.asarray(first.animation["vert_frames"][-1]).copy()
    finally:
        first.cleanup()

    bend.angle = 0.8
    bend.keyframe_insert("angle", frame=3)
    second = solver_test._capture_collider_motion(bpy.context, source, bake_range)
    try:
        assert second.motion_type == "DEFORMING_ANIMATED"
        assert second.content_digest != first_digest
        assert not np.allclose(second.animation["vert_frames"][-1], first_last)
    finally:
        second.cleanup()
    return first_digest, second.content_digest


''' + smoke_anchor
replace_exact(smoke_path, smoke_anchor, smoke_block)

replace_exact(
    smoke_path,
    '''    simple_source, simple_proxy = _simple_proxy_smoke(scene)
    cage_source, cage_segments, cage_vertices = _character_cage_smoke(scene)
''',
    '''    simple_source, simple_proxy = _simple_proxy_smoke(scene)
    first_digest, second_digest = _animated_collider_cache_identity_smoke(scene)
    cage_source, cage_segments, cage_vertices = _character_cage_smoke(scene)
''')
replace_exact(
    smoke_path,
    '''        "animated": True,
''',
    '''        "animated": True,
        "collider_digest_changed": first_digest != second_digest,
''')

artifact_path = "tests/test_built_artifacts.py"
replace_exact(
    artifact_path,
    '''    assert "SCENE_EXPORT_CACHE_SCHEMA" in solver_test
''',
    '''    assert "SCENE_EXPORT_CACHE_SCHEMA" in solver_test
    assert '"animation_digest"' in solver_test
    assert "content_digest=motion_hasher.hexdigest()" in solver_test
''')

for path in (
    Path("maintenance/apply_animated_collider_cache_fix.py"),
    Path("maintenance/axis-motion-regression-marker.txt"),
):
    if path.exists():
        path.unlink()
