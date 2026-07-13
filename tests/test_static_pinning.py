# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import FrozenInstanceError

import pytest

from cloth_next.materials import DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS
from cloth_next.pinning import (STATIC_PIN_WEIGHT_THRESHOLD, StaticPinError,
                                StaticPinSnapshot, static_pin_config)
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.schema.data import SceneObject, build_scene_payload
from cloth_next.ppf.schema.params import SimulationSettings, build_param_payload


def snapshot(enabled=True, indices=(7, 2, 7)):
    return StaticPinSnapshot(enabled, "ShoulderPins" if enabled else "",
                             "cloth-stable-id", 10, indices)


def test_disabled_pinning_produces_no_config():
    assert static_pin_config(snapshot(False, ())) is None


def test_static_pin_config_is_hard_immutable_and_deterministic():
    snap = snapshot()
    config = static_pin_config(snap)
    assert snap.vertex_indices == (2, 7)
    assert config.indices == (2, 7)
    assert config.operations == () and config.unpin_time is None
    assert config.transition == "linear" and config.pull_strength == 0.0
    assert config.pull_weights is None and config.pin_stiffness == 1.0
    assert config.rest_shape_track is False
    assert config.pin_group_id == static_pin_config(snapshot()).pin_group_id
    with pytest.raises(FrozenInstanceError):
        config.pull_strength = 2.0


@pytest.mark.parametrize("indices", [(), (-1,), (10,)])
def test_invalid_enabled_membership_is_rejected(indices):
    with pytest.raises(StaticPinError):
        snapshot(indices=indices)


def test_fingerprint_uses_binary_membership_not_weights():
    a = snapshot(indices=(2, 7))
    b = snapshot(indices=(7, 2))
    c = snapshot(indices=(2, 8))
    assert a.fingerprint == b.fingerprint
    assert a.fingerprint != c.fingerprint
    assert a.threshold == STATIC_PIN_WEIGHT_THRESHOLD


def test_fingerprint_changes_with_source_topology_signature():
    a = StaticPinSnapshot(True, "Pins", "id", 10, (1,),
                          source_topology_signature="topology-a")
    b = StaticPinSnapshot(True, "Pins", "id", 10, (1,),
                          source_topology_signature="topology-b")
    assert a.fingerprint != b.fingerprint


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
    assert all(value == {"pin_group_id": cfg.pin_group_id, "operations": []}
               for value in payload["pin_config"]["cloth-id"].values())


def test_disabled_scene_emits_no_stale_pin_fields():
    ident = solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0),
                                 (0, 0, 1, 0), (0, 0, 0, 1)))
    obj = SceneObject("Cloth", "id", ((0., 0., 0.), (1., 0., 0.),
                      (0., 1., 0.)), ((0, 1, 2),), ident)
    assert "pin" not in obj.info_dict()
