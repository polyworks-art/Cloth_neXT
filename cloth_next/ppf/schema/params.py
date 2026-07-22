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

Scene keys include ``dt`` (1e-3 s solver default), axis-swapped ``gravity``
and ``wind``, optional ``air-density``, ``air-friction`` and
``isotropic-air-friction``,
``frames`` (Blender N -> solver N-1), ``fps``, ``friction-mode`` "min"
(solver default combination mode; both surfaces need high friction for a
grippy contact), and ``disable-contact`` from the cloth's Enable Contact
toggle. Native Blender Force animation is sampled into PPF's ``dyn_param``
tracks using the same audited scene keys.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from ...materials import WIRE_MODEL_NAMES, ShellMaterialSettings, StaticMaterialSettings
from ...materials.validation import validate_shell_values, validate_static_values
from ...materials.deformables import (RodMaterialSettings,
                                      SoftBodyMaterialSettings)
from ...pinning import StaticPinConfig
from ...solver_quality import DEFAULT_SOLVER_QUALITY, SolverQualitySettings
from ..coordinates import blender_vector_to_ppf
from . import envelope

FIXED_TIME_STEP = DEFAULT_SOLVER_QUALITY.time_step  # compatibility export

FRICTION_MODE = "min"  # fixed this phase; surfaced read-only in Advanced PPF
_DYNAMIC_VECTOR_KEYS = {"gravity", "wind"}
_DYNAMIC_SCALAR_KEYS = {
    "air-density", "air-friction", "isotropic-air-friction"}


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
    # Shell shrink is intentionally disabled until upstream PPF can combine
    # changing rest lengths with its strain limiter.  Keep the legacy setting
    # readable so old .blend files and presets remain compatible.
    shrink_factor = 1.0
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
        "shrink-x": float32_wire(shrink_factor),
        "shrink-y": float32_wire(shrink_factor),
        "pressure": float32_wire(shell.inflate_pressure
                                 if shell.enable_inflate else 0.0),
        "stitch-stiffness": float32_wire(shell.sewing_stiffness),
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


def rod_wire_params(rod: RodMaterialSettings) -> dict[str, object]:
    return {
        "model": "arap", "density": float32_wire(rod.linear_density),
        "young-mod": float32_wire(rod.stretch_resistance),
        "friction": float32_wire(rod.surface_grip),
        "deformation-damping": float32_wire(rod.shape_damping),
        "bending-damping": float32_wire(rod.bend_damping),
        "contact-gap": float32_wire(rod.collision_gap),
        "contact-offset": float32_wire(rod.surface_offset),
        "bend": float32_wire(rod.bend_resistance),
        "strain-limit": float32_wire(rod.stretch_limit),
        "length-factor": float32_wire(rod.length_factor),
    }


def soft_body_wire_params(soft: SoftBodyMaterialSettings, uuid: str) -> dict[str, object]:
    tet = {} if soft.tetrahedralizer == "ftetwild" else {uuid: {"backend": "tetgen"}}
    return {
        "model": "arap", "density": float32_wire(soft.volume_density),
        "young-mod": float32_wire(soft.stretch_resistance),
        "poiss-rat": float32_wire(soft.poisson_ratio),
        "shrink": float32_wire(soft.volume_scale),
        "friction": float32_wire(soft.surface_grip),
        "deformation-damping": float32_wire(soft.shape_damping),
        "contact-gap": float32_wire(soft.collision_gap),
        "contact-offset": float32_wire(soft.surface_offset),
        "ftetwild": tet,
    }


@dataclass(frozen=True, slots=True)
class SimulationSettings:
    """Immutable scene-level inputs for the vertical slice."""

    frame_count: int  # Blender frames 1..frame_count
    fps: int
    gravity_blender: tuple[float, float, float]
    quality: SolverQualitySettings = field(default_factory=SolverQualitySettings)
    wind_blender: tuple[float, float, float] = (0.0, 0.0, 0.0)
    air_density: float | None = None
    air_friction: float | None = None
    vertex_air_damp: float | None = None
    dynamic_parameters: tuple[
        tuple[str, tuple[tuple[float, tuple[float, ...], bool], ...]], ...
    ] = ()

    def __post_init__(self) -> None:
        if self.frame_count < 2:
            raise ParamEncodeError("frame_count must be at least 2")
        if self.fps < 1:
            raise ParamEncodeError("fps must be at least 1")
        if len(self.gravity_blender) != 3 or any(
                not math.isfinite(c) for c in self.gravity_blender):
            raise ParamEncodeError("gravity must be a finite 3-vector")
        if len(self.wind_blender) != 3 or any(
                not math.isfinite(c) for c in self.wind_blender):
            raise ParamEncodeError("wind must be a finite 3-vector")
        for name, value in (("air density", self.air_density),
                            ("air friction", self.air_friction),
                            ("vertex air damping", self.vertex_air_damp)):
            if value is not None and (not math.isfinite(value) or value < 0.0):
                raise ParamEncodeError(f"{name} must be finite and non-negative")
        seen = set()
        for key, entries in self.dynamic_parameters:
            if key not in _DYNAMIC_VECTOR_KEYS | _DYNAMIC_SCALAR_KEYS:
                raise ParamEncodeError(f"unsupported dynamic parameter {key!r}")
            if key in seen:
                raise ParamEncodeError(f"duplicate dynamic parameter {key!r}")
            seen.add(key)
            if len(entries) < 2:
                raise ParamEncodeError(
                    f"dynamic parameter {key!r} needs at least two samples")
            previous = -1.0
            expected = 3 if key in _DYNAMIC_VECTOR_KEYS else 1
            for time_seconds, value, _hold in entries:
                if (not math.isfinite(time_seconds) or time_seconds < 0.0
                        or time_seconds <= previous):
                    raise ParamEncodeError(
                        f"dynamic parameter {key!r} times must increase")
                if (len(value) != expected
                        or any(not math.isfinite(component) for component in value)
                        or (key in _DYNAMIC_SCALAR_KEYS
                            and any(component < 0.0 for component in value))):
                    raise ParamEncodeError(
                        f"dynamic parameter {key!r} has an invalid value")
                previous = time_seconds


def _scene_wire_params(settings: SimulationSettings,
                       contact_enabled: bool) -> dict:
    scene = {
        "dt": float32_wire(settings.quality.time_step),
        "min-newton-steps": int(settings.quality.min_newton_steps),
        "cg-max-iter": int(settings.quality.cg_max_iter),
        "cg-tol": float32_wire(settings.quality.cg_tol),
        "gravity": list(blender_vector_to_ppf(settings.gravity_blender)),
        "wind": [0.0 if value == 0.0 else value
                 for value in blender_vector_to_ppf(settings.wind_blender)],
        # Blender frames 1..N map to solver frames 0..N-1 (upstream contract).
        "frames": int(settings.frame_count) - 1,
        "fps": int(settings.fps),
        "friction-mode": FRICTION_MODE,
        "disable-contact": not bool(contact_enabled),
    }
    for key, value in (("air-density", settings.air_density),
                       ("air-friction", settings.air_friction),
                       ("isotropic-air-friction", settings.vertex_air_damp)):
        if value is not None:
            scene[key] = float32_wire(value)
    return scene


def _attach_dynamic_params(payload: dict, settings: SimulationSettings) -> dict:
    if not settings.dynamic_parameters:
        return payload
    dynamic = {}
    for key, entries in settings.dynamic_parameters:
        encoded = []
        for time_seconds, value, hold in entries:
            wire_value = (list(blender_vector_to_ppf(value))
                          if key in _DYNAMIC_VECTOR_KEYS
                          else [float32_wire(value[0])])
            encoded.append((float(time_seconds), wire_value, bool(hold)))
        dynamic[key] = encoded
    payload["dyn_param"] = dynamic
    return payload


def build_param_payload(settings: SimulationSettings,
                        cloth_name: str, cloth_uuid: str,
                        collider_name: str, collider_uuid: str, *,
                        shell: ShellMaterialSettings,
                        static: StaticMaterialSettings,
                        contact_enabled: bool = True,
                        static_pin: StaticPinConfig | None = None) -> dict:
    for label, value in (("cloth name", cloth_name), ("cloth uuid", cloth_uuid),
                         ("collider name", collider_name),
                         ("collider uuid", collider_uuid)):
        if not value.strip():
            raise ParamEncodeError(f"{label} must not be empty")
    scene = _scene_wire_params(settings, contact_enabled)
    group = [
        (shell_wire_params(shell), [cloth_name], [cloth_uuid]),
        (static_wire_params(static), [collider_name], [collider_uuid]),
    ]
    # A plain hard hold is established by SceneObject.pin.  Upstream's
    # pin_config is optional behavior; retaining only the deterministic group
    # identity avoids turning pull_strength=0 into the soft-pull code path.
    pin_config = {}
    if static_pin is not None:
        pin_config[cloth_uuid] = {}
        for offset,index in enumerate(static_pin.indices):
            config={"pin_group_id":static_pin.pin_group_id,"operations":[]}
            if static_pin.times:
                config["embedded_move_index"]=0
                config["pin_anim"]={index:{"time":list(static_pin.times),
                    "position":[list(frame[offset]) for frame in static_pin.positions]}}
            pin_config[cloth_uuid][index]=config
    payload = {"scene": scene, "group": group, "pin_config": pin_config}
    return _attach_dynamic_params(payload, settings)


def build_multi_collider_param_payload(
        settings: SimulationSettings, cloth_name: str, cloth_uuid: str,
        colliders, *, shell: ShellMaterialSettings,
        contact_enabled: bool = True,
        static_pin: StaticPinConfig | None = None) -> dict:
    """PPF Param payload with one material group per STATIC collider."""
    entries = tuple(colliders)
    if not entries:
        return build_multi_deformable_param_payload(
            settings, ((cloth_name, cloth_uuid, "SHELL", shell,
                        static_pin),), (), contact_enabled=contact_enabled)
    first_name, first_uuid, first_static = entries[0]
    payload = build_param_payload(
        settings, cloth_name, cloth_uuid, first_name, first_uuid,
        shell=shell, static=first_static, contact_enabled=contact_enabled,
        static_pin=static_pin)
    payload["group"] = [payload["group"][0]] + [
        (static_wire_params(static), [name], [uuid])
        for name, uuid, static in entries]
    return payload


def encode_multi_collider_param(
        settings: SimulationSettings, cloth_name: str, cloth_uuid: str,
        colliders, *, shell: ShellMaterialSettings,
        contact_enabled: bool = True,
        static_pin: StaticPinConfig | None = None) -> tuple[bytes, str]:
    payload = build_multi_collider_param_payload(
        settings, cloth_name, cloth_uuid, colliders, shell=shell,
        contact_enabled=contact_enabled, static_pin=static_pin)
    blob = envelope.dumps_envelope(envelope.KIND_PARAM, payload)
    return blob, envelope.payload_sha256(blob)


def build_deformable_param_payload(
        settings: SimulationSettings, deformable_name: str,
        deformable_uuid: str, colliders, *, group_type: str,
        material: ShellMaterialSettings | RodMaterialSettings |
        SoftBodyMaterialSettings, contact_enabled: bool = True,
        static_pin: StaticPinConfig | None = None) -> dict:
    return build_multi_deformable_param_payload(
        settings,
        ((deformable_name, deformable_uuid, group_type, material,
          static_pin),),
        colliders, contact_enabled=contact_enabled)


def build_multi_deformable_param_payload(
        settings: SimulationSettings, deformables, colliders, *,
        contact_enabled: bool = True) -> dict:
    """PPF parameters for multiple dynamic objects and shared colliders.

    Each deformable tuple is ``(name, uuid, group_type, material, pin)``.
    Separate groups deliberately preserve per-object material settings.
    """
    dynamic_entries = tuple(deformables)
    if not dynamic_entries:
        raise ParamEncodeError("at least one deformable is required")
    entries = tuple(colliders)
    dynamic_groups = []
    pin_config = {}
    seen = set()
    for name, uuid, group_type, material, static_pin in dynamic_entries:
        if not name.strip() or not uuid.strip():
            raise ParamEncodeError("deformable name and uuid must not be empty")
        if uuid in seen:
            raise ParamEncodeError(f"duplicate deformable uuid {uuid!r}")
        seen.add(uuid)
        if group_type == "SHELL" and isinstance(material, ShellMaterialSettings):
            params = shell_wire_params(material)
        elif group_type == "ROD" and isinstance(material, RodMaterialSettings):
            params = rod_wire_params(material)
        elif group_type == "SOLID" and isinstance(material, SoftBodyMaterialSettings):
            params = soft_body_wire_params(material, uuid)
        else:
            raise ParamEncodeError(
                f"material does not match deformable group {group_type!r}")
        dynamic_groups.append((params, [name], [uuid]))
        if static_pin is not None:
            object_pins = {}
            for offset, index in enumerate(static_pin.indices):
                config = {"pin_group_id": static_pin.pin_group_id,
                          "operations": []}
                if static_pin.times:
                    config["embedded_move_index"] = 0
                    config["pin_anim"] = {
                        index: {"time": list(static_pin.times),
                                "position": [list(frame[offset])
                                             for frame in static_pin.positions]}}
                object_pins[index] = config
            pin_config[uuid] = object_pins
    payload = {"scene": _scene_wire_params(settings, contact_enabled),
               "group": [
        *dynamic_groups,
        *((static_wire_params(static), [name], [uuid])
          for name, uuid, static in entries),
    ], "pin_config": pin_config}
    return _attach_dynamic_params(payload, settings)


def encode_deformable_param(
        settings: SimulationSettings, deformable_name: str,
        deformable_uuid: str, colliders, *, group_type: str,
        material: ShellMaterialSettings | RodMaterialSettings |
        SoftBodyMaterialSettings, contact_enabled: bool = True,
        static_pin: StaticPinConfig | None = None) -> tuple[bytes, str]:
    payload = build_deformable_param_payload(
        settings, deformable_name, deformable_uuid, colliders,
        group_type=group_type, material=material,
        contact_enabled=contact_enabled, static_pin=static_pin)
    blob = envelope.dumps_envelope(envelope.KIND_PARAM, payload)
    return blob, envelope.payload_sha256(blob)


def encode_multi_deformable_param(
        settings: SimulationSettings, deformables, colliders, *,
        contact_enabled: bool = True) -> tuple[bytes, str]:
    payload = build_multi_deformable_param_payload(
        settings, deformables, colliders, contact_enabled=contact_enabled)
    blob = envelope.dumps_envelope(envelope.KIND_PARAM, payload)
    return blob, envelope.payload_sha256(blob)


def encode_param(settings: SimulationSettings,
                 cloth_name: str, cloth_uuid: str,
                 collider_name: str, collider_uuid: str, *,
                 shell: ShellMaterialSettings,
                 static: StaticMaterialSettings,
                 contact_enabled: bool = True,
                 static_pin: StaticPinConfig | None = None) -> tuple[bytes, str]:
    payload = build_param_payload(settings, cloth_name, cloth_uuid,
                                  collider_name, collider_uuid,
                                  shell=shell, static=static,
                                  contact_enabled=contact_enabled,
                                  static_pin=static_pin)
    blob = envelope.dumps_envelope(envelope.KIND_PARAM, payload)
    return blob, envelope.payload_sha256(blob)
