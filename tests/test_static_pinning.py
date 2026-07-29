# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import FrozenInstanceError

import pytest

from cloth_next.materials import DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS
from cloth_next.pinning import (
    STATIC_PIN_WEIGHT_THRESHOLD,
    AnimatedPinTargetSample,
    PinConstraintType,
    PinMode,
    StaticPinError,
    StaticPinSnapshot,
    set_pin_constraint_resolver,
    static_pin_config,
)
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.schema.data import SceneObject, build_scene_payload
from cloth_next.ppf.schema.params import SimulationSettings, build_param_payload


def snapshot(enabled=True, indices=(7, 2, 7), *,
             constraint_type=PinConstraintType.SOFT, pull_strength=1.0):
    return StaticPinSnapshot(
        enabled, "ShoulderPins" if enabled else "", "cloth-stable-id", 10,
        indices, constraint_type=constraint_type, pull_strength=pull_strength)


def test_disabled_pinning_produces_no_config():
    assert static_pin_config(snapshot(False, ())) is None


def test_static_pin_config_is_soft_immutable_and_deterministic_by_default():
    snap = snapshot()
    config = static_pin_config(snap)
    assert snap.vertex_indices == (2, 7)
    assert snap.constraint_type is PinConstraintType.SOFT
    assert config.indices == (2, 7)
    assert config.operations == () and config.unpin_time is None
    assert config.transition == "linear" and config.pull_strength == 1.0
    assert config.pull_weights is None and config.pin_stiffness == 1.0
    assert config.rest_shape_track is False
    assert config.pin_group_id == static_pin_config(snapshot()).pin_group_id
    with pytest.raises(FrozenInstanceError):
        config.pull_strength = 2.0


def test_hard_pin_normalizes_pull_strength_to_zero():
    snap = snapshot(
        constraint_type=PinConstraintType.HARD, pull_strength=250.0)
    config = static_pin_config(snap)
    assert snap.constraint_type is PinConstraintType.HARD
    assert snap.pull_strength == 0.0
    assert config.pull_strength == 0.0


@pytest.mark.parametrize("indices", [(), (-1,), (10,)])
def test_invalid_enabled_membership_is_rejected(indices):
    with pytest.raises(StaticPinError):
        snapshot(indices=indices)


def test_soft_pin_requires_positive_finite_strength():
    with pytest.raises(StaticPinError):
        snapshot(pull_strength=0.0)
    with pytest.raises(StaticPinError):
        snapshot(pull_strength=float("nan"))


def test_fingerprint_uses_binary_membership_not_weights():
    a = snapshot(indices=(2, 7))
    b = snapshot(indices=(7, 2))
    c = snapshot(indices=(2, 8))
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint
    assert a.threshold == STATIC_PIN_WEIGHT_THRESHOLD


def test_fingerprint_changes_with_source_topology_signature_and_constraint():
    a = StaticPinSnapshot(True, "Pins", "id", 10, (1,),
                          source_topology_signature="topology-a")
    b = StaticPinSnapshot(True, "Pins", "id", 10, (1,),
                          source_topology_signature="topology-b")
    hard = StaticPinSnapshot(
        True, "Pins", "id", 10, (1,),
        source_topology_signature="topology-a",
        constraint_type=PinConstraintType.HARD)
    stronger = StaticPinSnapshot(
        True, "Pins", "id", 10, (1,),
        source_topology_signature="topology-a", pull_strength=2.0)
    assert a.fingerprint != b.fingerprint
    assert a.fingerprint != hard.fingerprint
    assert a.fingerprint != stronger.fingerprint


def test_blender_constraint_resolver_overrides_snapshot_defaults():
    set_pin_constraint_resolver(
        lambda object_id, group_name: (
            PinConstraintType.HARD, 999.0)
        if (object_id, group_name) == ("cloth-stable-id", "ShoulderPins")
        else None)
    try:
        snap = snapshot()
        assert snap.constraint_type is PinConstraintType.HARD
        assert snap.pull_strength == 0.0
    finally:
        set_pin_constraint_resolver(None)


def test_ppf_static_pin_scene_and_param_contract():
    ident = solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0),
                                 (0, 0, 1, 0), (0, 0, 0, 1)))
    cloth = SceneObject("Cloth", "cloth-id",
                        ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.)),
                        ((0, 1, 2),), ident, (0, 2))
    collider = SceneObject("Floor", "floor-id",
                           ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.)),
                           ((0, 1, 2),), ident)
    assert build_scene_payload(cloth, collider)[0]["object"][0]["pin"] == [0, 2]
    cfg = static_pin_config(snapshot(indices=(0, 2)))
    payload = build_param_payload(
        SimulationSettings(2, 24, (0., 0., -9.81)), "Cloth", "cloth-id",
        "Floor", "floor-id", shell=DEFAULT_SHELL_SETTINGS,
        static=DEFAULT_STATIC_SETTINGS, static_pin=cfg)
    assert set(payload["pin_config"]) == {"cloth-id"}
    assert set(payload["pin_config"]["cloth-id"]) == {0, 2}
    assert all(value == {
        "pin_group_id": cfg.pin_group_id,
        "operations": [],
        "pull_strength": pytest.approx(1.0),
    } for value in payload["pin_config"]["cloth-id"].values())


def test_ppf_hard_pin_omits_soft_pull_strength():
    cfg = static_pin_config(snapshot(
        indices=(0, 2), constraint_type=PinConstraintType.HARD))
    payload = build_param_payload(
        SimulationSettings(2, 24, (0., 0., -9.81)), "Cloth", "cloth-id",
        "Floor", "floor-id", shell=DEFAULT_SHELL_SETTINGS,
        static=DEFAULT_STATIC_SETTINGS, static_pin=cfg)
    assert all("pull_strength" not in value
               for value in payload["pin_config"]["cloth-id"].values())


def test_disabled_scene_emits_no_stale_pin_fields():
    ident = solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0),
                                 (0, 0, 1, 0), (0, 0, 0, 1)))
    obj = SceneObject("Cloth", "id", ((0., 0., 0.), (1., 0., 0.),
                      (0., 1., 0.)), ((0, 1, 2),), ident)
    assert "pin" not in obj.info_dict()


def animated(mode=PinMode.FOLLOW_ANIMATION, offset=0., *,
             constraint_type=PinConstraintType.SOFT):
    samples = tuple(AnimatedPinTargetSample(
        frame, ((offset + frame, 0., 0.), (0., frame, 0.)))
        for frame in range(20, 31)) if mode is PinMode.FOLLOW_ANIMATION else ()
    return StaticPinSnapshot(
        True, "Pins", "id", 4, (0, 2), mode=mode, samples=samples,
        bake_start=20, bake_end=30, fps=25,
        constraint_type=constraint_type)


def test_animated_pin_model_and_time_mapping_are_immutable():
    snap = animated()
    cfg = static_pin_config(snap)
    assert len(snap.samples) == 11 and cfg.times[0] == 0 and cfg.times[-1] == .4
    assert cfg.positions[0][0] == (20., 0., 0.)
    with pytest.raises(FrozenInstanceError):
        snap.samples[0].blender_frame = 99


def test_animated_pin_subframes_keep_exact_solver_times_in_both_schemas():
    frames = (20.0, 20.25, 20.5, 20.75, 21.0)
    samples = tuple(
        AnimatedPinTargetSample(
            frame, ((frame, 0.0, 0.0), (0.0, frame, 0.0)))
        for frame in frames)
    snap = StaticPinSnapshot(
        True, "Pins", "id", 4, (0, 2),
        mode=PinMode.FOLLOW_ANIMATION, samples=samples,
        bake_start=20, bake_end=21, fps=25)
    cfg = static_pin_config(snap)
    assert cfg.times == pytest.approx((0.0, 0.01, 0.02, 0.03, 0.04))

    tracks = []
    for schema_version in (1, 2):
        payload = build_param_payload(
            SimulationSettings(2, 25, (0.0, 0.0, -9.81)),
            "Cloth", "cloth-id", "Floor", "floor-id",
            shell=DEFAULT_SHELL_SETTINGS, static=DEFAULT_STATIC_SETTINGS,
            static_pin=cfg, schema_version=schema_version)
        entry = payload["pin_config"]["cloth-id"][0]
        assert entry["pull_strength"] == pytest.approx(1.0)
        tracks.append(entry["pin_anim"][0])
    assert tracks[0] == tracks[1]
    assert tracks[1]["time"] == pytest.approx(cfg.times)
    assert len(tracks[1]["position"]) == 5


def test_animated_pin_validation_and_fingerprint():
    with pytest.raises(StaticPinError):
        StaticPinSnapshot(
            True, "Pins", "id", 4, (0,), mode=PinMode.FOLLOW_ANIMATION,
            bake_start=1, bake_end=2, samples=())
    with pytest.raises(StaticPinError):
        AnimatedPinTargetSample(1, ((float("nan"), 0, 0),))
    with pytest.raises(StaticPinError):
        AnimatedPinTargetSample(float("nan"), ((0, 0, 0),))
    assert animated(PinMode.STATIC).samples == ()
    assert animated().fingerprint != animated(PinMode.STATIC).fingerprint
    assert animated().fingerprint != animated(offset=1.).fingerprint


def test_ppf_follow_animation_emits_per_vertex_tracks_and_soft_pull():
    cfg = static_pin_config(animated())
    payload = build_param_payload(
        SimulationSettings(11, 25, (0, 0, -9.81)), "Cloth", "cloth-id",
        "Floor", "floor-id", shell=DEFAULT_SHELL_SETTINGS,
        static=DEFAULT_STATIC_SETTINGS, static_pin=cfg)
    entries = payload["pin_config"]["cloth-id"]
    assert set(entries) == {0, 2}
    for index, entry in entries.items():
        assert entry["operations"] == []
        assert entry["pull_strength"] == pytest.approx(1.0)
        assert "unpin_time" not in entry
        track = entry["pin_anim"][index]
        assert track["time"][0] == 0
        assert track["time"][-1] == .4
        assert len(track["position"]) == 11


def test_protocol_013_pin_times_follow_time_scaled_solver_rate():
    samples = tuple(
        AnimatedPinTargetSample(
            frame, ((float(frame), 0.0, 0.0), (0.0, float(frame), 0.0)))
        for frame in (20.0, 21.0))
    snapshot = StaticPinSnapshot(
        True, "Pins", "id", 4, (0, 2),
        mode=PinMode.FOLLOW_ANIMATION, samples=samples,
        bake_start=20, bake_end=21, fps=25, time_scale=0.5)

    legacy = static_pin_config(snapshot, schema_version=1)
    current = static_pin_config(snapshot, schema_version=2)

    assert legacy.times == pytest.approx((0.0, 0.04))
    assert current.times == pytest.approx((0.0, 0.08))
    assert legacy.positions == current.positions


def test_pin_time_scale_is_validated_and_fingerprinted():
    with pytest.raises(StaticPinError, match="Time Scale"):
        StaticPinSnapshot(
            True, "Pins", "id", 4, (0,), time_scale=0.0)
    assert animated().fingerprint != StaticPinSnapshot(
        True, "Pins", "id", 4, (0, 2), mode=PinMode.FOLLOW_ANIMATION,
        samples=animated().samples, bake_start=20, bake_end=30, fps=25,
        time_scale=0.5).fingerprint
