# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""PPF 0.11 Param payload with the real Phase-3B material mapping.

Exact reproduction of the subset of ``kinds/param.rs`` /
``encoder/params.py`` (pinned commit ``7193f158``) the vertical slice
needs:

``{"scene": {...}, "group": [(shell_params, [name], [uuid]),
                             (static_params, [name], [uuid])],
  "pin_config": {}}``

All keys are the upstream kebab-case spellings; the consumer forwards each
present key to the solver's parameter table (unknown keys are a hard error
there, absent keys fall back to the solver defaults), so only audited keys
are emitted.

Material values come from the immutable, validated
:class:`~cloth_next.materials.ShellMaterialSettings` /
:class:`~cloth_next.materials.StaticMaterialSettings` captured on Blender's
main thread. The full artist-name -> wire-key table lives in
``docs/PPF_PARAMETER_MAPPING.md``. Two invariants matter most:

- ``stretch_resistance`` is the DIRECT density-normalized ``young-mod``
  wire value. It is NEVER divided by density here (the upstream presets
  are calibrated in this convention; dividing again would silently soften
  every fabric by its density factor).
- Material floats are rounded through IEEE-754 float32 before the CBOR
  float64 encode, mirroring the upstream encoder's ``np.float32(...)``
  wrapping, so Cloth NeXt payload values match the official client's
  bit-for-bit.

Scene keys (unchanged from Phase 3A except ``disable-contact``):
``dt`` (1e-3 s solver default), swapped Blender ``gravity``, zero ``wind``,
``frames`` (Blender N -> solver N-1), ``fps``, ``friction-mode`` "min"
(solver default combination mode; both surfaces need high friction for a
grippy contact), and ``disable-contact`` from the cloth's Enable Contact
toggle.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from ...materials import (ShellMaterialSettings, StaticMaterialSettings,
                          WIRE_MODEL_NAMES)
from ...materials.validation import (validate_shell_values,
                                     validate_static_values)
from ..coordinates import blender_vector_to_ppf
from . import envelope

FIXED_TIME_STEP = 1e-3  # seconds; upstream solver default

FRICTION_MODE = "min"  # fixed this phase; surfaced read-only in Advanced PPF


class ParamEncodeError(ValueError):
    pass


def float32_wire(value: float) -> float:
    """Round to the exact float32 the solver's parameter table stores.

    The upstream encoder wraps every material scalar in ``np.float32``;
    reproducing that here keeps payloads and hashes comparable with the
    official client and makes the precision loss explicit and tested.
    """
    return float(struct.unpack(">f", struct.pack(">f", float(value)))[0])


def shell_wire_params(shell: ShellMaterialSettings) -> dict[str, object]:
    """The exact SHELL group parameter dict for the pinned solver.

    ``young-mod`` receives ``stretch_resistance`` unchanged — no density
    division (see the module docstring).
    """
    validate_shell_values(shell)
    strain_limit = (shell.maximum_stretch_percent / 100.0
                    if shell.stretch_limit_enabled else 0.0)
    return {
        "model": WIRE_MODEL_NAMES[shell.model],
        "density": float32_wire(shell.surface_weight),
        "young-mod": float32_wire(shell.stretch_resistance),
        "poiss-rat": float32_wire(shell.sideways_response),
        "bend": float32_wire(shell.bend_resistance),
        "deformation-damping": float32_wire(shell.shape_damping),
        "bending-damping": float32_wire(shell.fold_damping),
        "friction": float32_wire(shell.surface_grip),
        "contact-gap": float32_wire(shell.collision_gap),
        "contact-offset": float32_wire(shell.surface_offset),
        "strain-limit": float32_wire(strain_limit),
    }


def static_wire_params(static: StaticMaterialSettings) -> dict[str, object]:
    """The exact STATIC group parameter dict (the only keys upstream
    emits for STATIC groups)."""
    validate_static_values(static)
    return {
        "friction": float32_wire(static.surface_grip),
        "contact-gap": float32_wire(static.collision_gap),
        "contact-offset": float32_wire(static.surface_offset),
    }


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
                        collider_name: str, collider_uuid: str, *,
                        shell: ShellMaterialSettings,
                        static: StaticMaterialSettings,
                        contact_enabled: bool = True) -> dict:
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
        "friction-mode": FRICTION_MODE,
        "disable-contact": not bool(contact_enabled),
    }
    group = [
        (shell_wire_params(shell), [cloth_name], [cloth_uuid]),
        (static_wire_params(static), [collider_name], [collider_uuid]),
    ]
    return {"scene": scene, "group": group, "pin_config": {}}


def encode_param(settings: SimulationSettings,
                 cloth_name: str, cloth_uuid: str,
                 collider_name: str, collider_uuid: str, *,
                 shell: ShellMaterialSettings,
                 static: StaticMaterialSettings,
                 contact_enabled: bool = True) -> tuple[bytes, str]:
    payload = build_param_payload(settings, cloth_name, cloth_uuid,
                                  collider_name, collider_uuid,
                                  shell=shell, static=static,
                                  contact_enabled=contact_enabled)
    blob = envelope.dumps_envelope(envelope.KIND_PARAM, payload)
    return blob, envelope.payload_sha256(blob)
