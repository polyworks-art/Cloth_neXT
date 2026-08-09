# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure product-level solver backend identities and capability declarations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BackendId(str, Enum):
    PPF = "PPF"


class MappingKind(str, Enum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"
    UNSUPPORTED = "UNSUPPORTED"
    SOLVER_SPECIFIC = "SOLVER_SPECIFIC"


@dataclass(frozen=True, slots=True)
class SolverCapabilities:
    live_preview: bool = False
    recovery: bool = False
    cloth: bool = False
    rods: bool = False
    soft_bodies: bool = False
    rigid_bodies: bool = False
    mixed_simulation: bool = False
    pressure: bool = False
    sewing: bool = False
    self_collision: bool = False
    animated_colliders: bool = False
    follow_animation_pins: bool = False
    gravity: bool = True
    wind: bool = False


@dataclass(frozen=True, slots=True)
class FieldMapping:
    kind: MappingKind
    detail: str


@dataclass(frozen=True, slots=True)
class SolverBackendSpec:
    identifier: BackendId
    display_name: str
    summary: str
    capabilities: SolverCapabilities
    material_mappings: tuple[tuple[str, FieldMapping], ...]

    def mapping_for(self, field: str) -> FieldMapping:
        try:
            return dict(self.material_mappings)[field]
        except KeyError as exc:
            raise KeyError(f"Unknown canonical material field: {field}") from exc


_EXACT_PPF = FieldMapping(
    MappingKind.EXACT, "Mapped directly to the verified PPF parameter.")
_CANONICAL_FIELDS = (
    "surface_weight", "stretch_resistance", "sideways_response",
    "bend_resistance", "stretch_limit", "shape_damping", "fold_damping",
    "surface_grip", "collision_gap", "surface_offset")

PPF_BACKEND = SolverBackendSpec(
    BackendId.PPF, "PPF", "High-fidelity contact-focused solver",
    SolverCapabilities(
        recovery=True, cloth=True, rods=True, soft_bodies=True,
        rigid_bodies=True, mixed_simulation=True, pressure=True, sewing=True,
        self_collision=True, animated_colliders=True,
        follow_animation_pins=True, wind=True),
    tuple((field, _EXACT_PPF) for field in _CANONICAL_FIELDS))

_BACKENDS = {PPF_BACKEND.identifier: PPF_BACKEND}


def backend_spec(identifier: BackendId | str) -> SolverBackendSpec:
    try:
        return _BACKENDS[BackendId(str(getattr(identifier, "value", identifier)))]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unknown Cloth NeXt solver backend: {identifier!r}") from exc


def default_backend() -> BackendId:
    return BackendId.PPF
