# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""PPF 0.11 Param payload for the Phase-3A vertical slice.

Exact reproduction of the subset of ``kinds/param.rs`` /
``encoder/params.py`` (pinned commit ``7193f158``) the 8-frame test needs:

``{"scene": {...}, "group": [(shell_params, [name], [uuid]),
                             (static_params, [name], [uuid])],
  "pin_config": {}}``

All keys are the upstream kebab-case spellings; the consumer forwards each
present key to the solver's parameter table (unknown keys are a hard error
there, absent keys fall back to the solver defaults), so only audited keys
are emitted.

Fixed Phase-3A values (documented in docs/PPF_PARAMETER_MAPPING.md; these
are NOT user-mapped yet):

Scene:
- ``dt``            1e-3 s      solver default step size
- ``gravity``       Blender scene gravity, axis-swapped to solver Y-up (m/s^2)
- ``wind``          (0, 0, 0)   no wind in this slice
- ``frames``        Blender frame count - 1 (Blender 1..N -> solver 0..N-1)
- ``fps``           Blender scene FPS (frame->time conversion)
- ``friction-mode`` "min"       solver default combination mode
- ``disable-contact`` False

SHELL material (upstream ``tri`` defaults unless noted):
- ``model``                "baraff-witkin"  (upstream tri default)
- ``density``              1000.0  kg/m^3 (upstream tri default)
- ``young-mod``            1.0     density-normalized Pa/rho (upstream tri
                                    default; a plain-Pa value must be divided
                                    by density before it lands here)
- ``poiss-rat``            0.35    dimensionless (upstream default)
- ``bend``                 10.0    bending stiffness (upstream tri default)
- ``deformation-damping``  0.0     (upstream default)
- ``bending-damping``      0.0     (upstream default)
- ``friction``             0.5     dimensionless Coulomb coefficient (chosen
                                    so the cloth visibly settles instead of
                                    sliding; legal range per upstream UI)
- ``contact-gap``          1e-3 m  (upstream default)
- ``contact-offset``       0.0  m  (upstream tri default)
- ``strain-limit``         0.0     disabled (upstream default)

STATIC collider:
- ``friction``       0.5, ``contact-gap`` 1e-3 m, ``contact-offset`` 0.0 m
  (the only keys the upstream encoder emits for STATIC groups)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..coordinates import blender_vector_to_ppf
from . import envelope

MODEL_BARAFF_WITKIN = "baraff-witkin"

FIXED_SHELL_MATERIAL: dict[str, object] = {
    "model": MODEL_BARAFF_WITKIN,
    "density": 1000.0,
    "young-mod": 1.0,
    "poiss-rat": 0.35,
    "bend": 10.0,
    "deformation-damping": 0.0,
    "bending-damping": 0.0,
    "friction": 0.5,
    "contact-gap": 1e-3,
    "contact-offset": 0.0,
    "strain-limit": 0.0,
}

FIXED_STATIC_MATERIAL: dict[str, object] = {
    "friction": 0.5,
    "contact-gap": 1e-3,
    "contact-offset": 0.0,
}

FIXED_TIME_STEP = 1e-3  # seconds; upstream solver default


class ParamEncodeError(ValueError):
    pass


def normalized_young_modulus(young_modulus_pa: float, density: float) -> float:
    """Apply the PPF density normalization: the wire carries Pa / rho."""
    if density <= 0:
        raise ParamEncodeError("density must be positive")
    return young_modulus_pa / density


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    """Immutable scene-level inputs for the vertical slice."""

    frame_count: int  # Blender frames 1..frame_count
    fps: int
    gravity_blender: tuple[float, float, float]
    time_step: float = FIXED_TIME_STEP

    def __post_init__(self) -> None:
        if self.frame_count < 2:
            raise ParamEncodeError("frame_count must be at least 2")
        if self.fps < 1:
            raise ParamEncodeError("fps must be at least 1")
        if not (self.time_step > 0 and math.isfinite(self.time_step)):
            raise ParamEncodeError("time_step must be positive and finite")
        if len(self.gravity_blender) != 3 or any(
                not math.isfinite(c) for c in self.gravity_blender):
            raise ParamEncodeError("gravity must be a finite 3-vector")


def build_param_payload(settings: SimulationSettings,
                        cloth_name: str, cloth_uuid: str,
                        collider_name: str, collider_uuid: str) -> dict:
    for label, value in (("cloth name", cloth_name), ("cloth uuid", cloth_uuid),
                         ("collider name", collider_name),
                         ("collider uuid", collider_uuid)):
        if not value.strip():
            raise ParamEncodeError(f"{label} must not be empty")
    scene = {
        "dt": float(settings.time_step),
        "gravity": list(blender_vector_to_ppf(settings.gravity_blender)),
        "wind": [0.0, 0.0, 0.0],
        # Blender frames 1..N map to solver frames 0..N-1 (upstream contract).
        "frames": int(settings.frame_count) - 1,
        "fps": int(settings.fps),
        "friction-mode": "min",
        "disable-contact": False,
    }
    group = [
        (dict(FIXED_SHELL_MATERIAL), [cloth_name], [cloth_uuid]),
        (dict(FIXED_STATIC_MATERIAL), [collider_name], [collider_uuid]),
    ]
    return {"scene": scene, "group": group, "pin_config": {}}


def encode_param(settings: SimulationSettings,
                 cloth_name: str, cloth_uuid: str,
                 collider_name: str, collider_uuid: str) -> tuple[bytes, str]:
    payload = build_param_payload(settings, cloth_name, cloth_uuid,
                                  collider_name, collider_uuid)
    blob = envelope.dumps_envelope(envelope.KIND_PARAM, payload)
    return blob, envelope.payload_sha256(blob)
