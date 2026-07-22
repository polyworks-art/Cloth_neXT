# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for permanently disabled PPF inactive momentum."""

from cloth_next.ppf.schema import envelope


def _decode(blob):
    return envelope.loads_envelope(blob, envelope.KIND_PARAM)


def test_param_envelope_removes_static_inactive_momentum_without_mutation():
    payload = {
        "scene": {"frames": 60, "inactive-momentum": 2.0},
        "group": [],
        "pin_config": {},
    }

    decoded = _decode(envelope.dumps_envelope(envelope.KIND_PARAM, payload))

    assert "inactive-momentum" not in decoded["scene"]
    assert payload["scene"]["inactive-momentum"] == 2.0


def test_param_envelope_removes_dynamic_inactive_momentum_only():
    payload = {
        "scene": {"frames": 60},
        "group": [],
        "pin_config": {},
        "dyn_param": {
            "inactive-momentum": [
                (0.0, [1.0], False),
                (2.0, [0.0], True),
            ],
            "gravity": [
                (0.0, [0.0, -9.81, 0.0], False),
                (2.0, [0.0, -9.81, 0.0], False),
            ],
        },
    }

    decoded = _decode(envelope.dumps_envelope(envelope.KIND_PARAM, payload))

    assert "inactive-momentum" not in decoded["dyn_param"]
    assert "gravity" in decoded["dyn_param"]
    assert "inactive-momentum" in payload["dyn_param"]


def test_non_param_envelopes_are_unchanged():
    payload = {"inactive-momentum": 2.0}

    decoded = envelope.loads_envelope(
        envelope.dumps_envelope(envelope.KIND_SCENE, payload),
        envelope.KIND_SCENE,
    )

    assert decoded == payload
