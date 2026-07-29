from pathlib import Path


def replace_exact(path: str, old: str, new: str, *, count: int = 1) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(
            f"{path}: expected {count} occurrence(s), found {actual}: {old[:100]!r}")
    file.write_text(text.replace(old, new), encoding="utf-8")


data_path = "cloth_next/ppf/schema/data.py"
helper_anchor = '''def _float32(value: float) -> float:
    """Round-trip through IEEE float32 so the wire carries exactly the
    precision the upstream encoder ships (numpy float32 -> CBOR double)."""
    return struct.unpack("<f", struct.pack("<f", value))[0]
'''
helper_replacement = helper_anchor + '''

def _schema2_full_frame_indices(name: str, sample_offsets):
    """Select the one captured pose that belongs to each Blender frame.

    Protocol 0.13 removed per-sample times from the DATA payload. Row ``i`` of
    ``static_deform_animation`` is therefore frame offset ``i``; dense Cloth
    NeXt sub-frame samples must not be serialized as additional full frames.
    """
    if sample_offsets is None:
        return None
    offsets = [float(value) for value in sample_offsets]
    if len(offsets) < 2:
        raise SceneEncodeError(
            f"{name}: collider animation needs at least two sample offsets")
    selected = []
    frame_offsets = []
    previous = -math.inf
    for index, value in enumerate(offsets):
        if not math.isfinite(value) or value <= previous:
            raise SceneEncodeError(
                f"{name}: collider sample offsets must be finite and increase")
        previous = value
        nearest = int(round(value))
        if abs(value - nearest) <= 1e-6:
            selected.append(index)
            frame_offsets.append(nearest)
    if not frame_offsets or frame_offsets[0] != 0:
        raise SceneEncodeError(
            f"{name}: collider capture must include Blender frame offset 0")
    expected = list(range(frame_offsets[-1] + 1))
    if frame_offsets != expected:
        raise SceneEncodeError(
            f"{name}: collider capture is missing a full Blender-frame pose")
    return tuple(selected), frame_offsets
'''
replace_exact(data_path, helper_anchor, helper_replacement)

transform_old = '''        if self.transform_animation is not None:
            animation = dict(self.transform_animation)
            if schema_version == 2:
                times = animation.pop("time")
                if len(times) > 1:
                    step = float(times[1]) - float(times[0])
                    if step <= 0.0:
                        raise SceneEncodeError(
                            f"{self.name}: animation times must increase")
                    animation["frame_offset"] = [
                        int(round((float(value) - float(times[0])) / step))
                        for value in times]
            info["transform_animation"] = animation
'''
transform_new = '''        if self.transform_animation is not None:
            animation = dict(self.transform_animation)
            sample_offsets = animation.pop("_sample_frame_offset", None)
            if schema_version == 2:
                times = animation.pop("time")
                frame_samples = _schema2_full_frame_indices(
                    self.name, sample_offsets)
                if frame_samples is not None:
                    indices, frame_offsets = frame_samples
                    for key in ("translation", "quaternion", "scale"):
                        animation[key] = [animation[key][index]
                                          for index in indices]
                    animation["segments"] = [
                        {"interpolation": "LINEAR",
                         "handle_right": [1.0 / 3.0, 0.0],
                         "handle_left": [2.0 / 3.0, 1.0]}
                        for _index in range(len(indices) - 1)]
                    animation["frame_offset"] = frame_offsets
                elif len(times) > 1:
                    step = float(times[1]) - float(times[0])
                    if step <= 0.0:
                        raise SceneEncodeError(
                            f"{self.name}: animation times must increase")
                    animation["frame_offset"] = [
                        int(round((float(value) - float(times[0])) / step))
                        for value in times]
            info["transform_animation"] = animation
'''
replace_exact(data_path, transform_old, transform_new)

deform_old = '''        if self.static_deform_animation is not None:
            animation = dict(self.static_deform_animation)
            if schema_version == 2:
                animation.pop("time", None)
            info["static_deform_animation"] = animation
'''
deform_new = '''        if self.static_deform_animation is not None:
            animation = dict(self.static_deform_animation)
            sample_offsets = animation.pop("_sample_frame_offset", None)
            if schema_version == 2:
                animation.pop("time", None)
                frame_samples = _schema2_full_frame_indices(
                    self.name, sample_offsets)
                if frame_samples is not None:
                    indices, _frame_offsets = frame_samples
                    frames = animation["vert_frames"]
                    try:
                        animation["vert_frames"] = frames[list(indices)]
                    except TypeError:
                        animation["vert_frames"] = [frames[index]
                                                    for index in indices]
            info["static_deform_animation"] = animation
'''
replace_exact(data_path, deform_old, deform_new)

solver_path = "cloth_next/blender/solver_test.py"
replace_exact(
    solver_path,
    "    times = [point[2] for point in sample_points]\n",
    "    times = [point[2] for point in sample_points]\n"
    "    frame_offsets = [point[0] + point[1] - bake_range.start\n"
    "                     for point in sample_points]\n",
    count=2)
replace_exact(
    solver_path,
    "            times = [sample[2] for sample in plans[obj.name]]\n",
    "            times = [sample[2] for sample in plans[obj.name]]\n"
    "            frame_offsets = [\n"
    "                sample[0] + sample[1] - bake_range.start\n"
    "                for sample in plans[obj.name]]\n")
replace_exact(
    solver_path,
    '{"time": times, "translation": translations,',
    '{"time": times, "_sample_frame_offset": frame_offsets, '
    '"translation": translations,',
    count=3)
replace_exact(
    solver_path,
    '{"time": times, "vert_frames": local_samples}',
    '{"time": times, "_sample_frame_offset": frame_offsets, '
    '"vert_frames": local_samples}')
replace_exact(
    solver_path,
    '{"time": times, "vert_frames": state["samples"]}',
    '{"time": times, "_sample_frame_offset": frame_offsets, '
    '"vert_frames": state["samples"]}')

params_path = "cloth_next/ppf/schema/params.py"
replace_exact(
    params_path,
    '        "fps": (float(settings.fps) if schema_version == 2\n'
    '                else int(settings.fps)),\n',
    '        # Protocol 0.13 uses the Time-Scaled solver rate. The\n'
    '        # collider schedule and physical integration must share it.\n'
    '        "fps": (float(settings.fps * settings.time_scale)\n'
    '                if schema_version == 2 else int(settings.fps)),\n')

tests_path = "tests/test_ppf_schema.py"
replace_exact(
    tests_path,
    '    assert param["payload"]["scene"]["fps"] == pytest.approx(23.976)\n',
    '    assert param["payload"]["scene"]["fps"] == pytest.approx(11.988)\n')

insertion_anchor = '''def test_multiple_colliders_keep_deterministic_scene_and_param_order():
'''
regression_tests = '''def test_schema2_dense_transform_capture_keeps_only_full_frames():
    offsets = [index / 8.0 for index in range(17)]
    collider = SceneObject(
        "Collider", "dense-transform-v2",
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((0, 1, 2),), solver_world_matrix(
            ((1, 0, 0, 0), (0, 1, 0, 0),
             (0, 0, 1, 0), (0, 0, 0, 1))),
        transform_animation={
            "time": [offset / 30.0 for offset in offsets],
            "_sample_frame_offset": offsets,
            "translation": [[offset, 0.0, 0.0] for offset in offsets],
            "quaternion": [[1.0, 0.0, 0.0, 0.0] for _ in offsets],
            "scale": [[1.0, 1.0, 1.0] for _ in offsets],
            "segments": [{"interpolation": "LINEAR"}
                         for _ in range(len(offsets) - 1)],
        })

    animation = collider.info_dict(
        schema_version=2)["transform_animation"]

    assert animation["frame_offset"] == [0, 1, 2]
    assert [row[0] for row in animation["translation"]] == [0.0, 1.0, 2.0]
    assert len(animation["segments"]) == 2
    assert "time" not in animation
    assert "_sample_frame_offset" not in animation


def test_schema2_dense_deforming_capture_keeps_only_full_frames():
    offsets = [index / 8.0 for index in range(17)]
    frames = np.asarray([
        np.full((3, 3), index, dtype=np.float64)
        for index in range(len(offsets))])
    collider = SceneObject(
        "Collider", "dense-deform-v2",
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((0, 1, 2),), solver_world_matrix(
            ((1, 0, 0, 0), (0, 1, 0, 0),
             (0, 0, 1, 0), (0, 0, 0, 1))),
        static_deform_animation={
            "time": [offset / 30.0 for offset in offsets],
            "_sample_frame_offset": offsets,
            "vert_frames": frames,
        })

    animation = collider.info_dict(
        schema_version=2)["static_deform_animation"]
    encoded = np.asarray(animation["vert_frames"])

    assert encoded.shape == (3, 3, 3)
    assert encoded[:, 0, 0].tolist() == [0.0, 8.0, 16.0]
    assert "time" not in animation
    assert "_sample_frame_offset" not in animation


def test_schema1_dense_capture_retains_subframes_without_private_metadata():
    offsets = [index / 8.0 for index in range(9)]
    frames = np.zeros((9, 3, 3), dtype=np.float64)
    collider = SceneObject(
        "Collider", "dense-deform-v1",
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((0, 1, 2),), solver_world_matrix(
            ((1, 0, 0, 0), (0, 1, 0, 0),
             (0, 0, 1, 0), (0, 0, 0, 1))),
        static_deform_animation={
            "time": [offset / 30.0 for offset in offsets],
            "_sample_frame_offset": offsets,
            "vert_frames": frames,
        })

    animation = collider.info_dict()["static_deform_animation"]

    assert len(animation["time"]) == 9
    assert np.asarray(animation["vert_frames"]).shape[0] == 9
    assert "_sample_frame_offset" not in animation


''' + insertion_anchor
replace_exact(tests_path, insertion_anchor, regression_tests)

for marker in (
    Path("maintenance/protocol-013-timebase-fix.txt"),
    Path("maintenance/apply_protocol_013_timebase_fix.py"),
    Path("maintenance/trigger-pr-protocol-013-timebase-fix.txt"),
):
    if marker.exists():
        marker.unlink()
