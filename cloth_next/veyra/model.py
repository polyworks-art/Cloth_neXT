# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable public model for the VEYRA process boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
from typing import Any


class CompanionMode(str, Enum):
    BAKE = "BAKE"
    VEYRA = "VEYRA"


class VeyraStep(str, Enum):
    ANALYZING_DIAGNOSTICS = "ANALYZING_DIAGNOSTICS"
    SOLVING_REPAIR_PLAN = "SOLVING_REPAIR_PLAN"
    APPLYING_REPAIRS = "APPLYING_REPAIRS"
    REVALIDATING_GEOMETRY = "REVALIDATING_GEOMETRY"
    VALIDATING_CONTACTS = "VALIDATING_CONTACTS"


VEYRA_STEP_LABELS = {
    VeyraStep.ANALYZING_DIAGNOSTICS: "Analyzing Diagnostics",
    VeyraStep.SOLVING_REPAIR_PLAN: "Solving Repair Plan",
    VeyraStep.APPLYING_REPAIRS: "Applying Repairs",
    VeyraStep.REVALIDATING_GEOMETRY: "Revalidating Geometry",
    VeyraStep.VALIDATING_CONTACTS: "Validating Contacts",
}


@dataclass(frozen=True, slots=True)
class RepairArtifact:
    schema: str
    job_id: str
    relative_path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RepairArtifact":
        if not isinstance(value, dict):
            raise ValueError("artifact metadata must be an object")
        result = cls(**{key: value[key] for key in cls.__dataclass_fields__})
        if (not result.schema or not result.job_id or not result.relative_path
                or result.size < 0 or len(result.sha256) != 64):
            raise ValueError("invalid artifact metadata")
        return result


@dataclass(frozen=True, slots=True)
class VertexDisplacement:
    object_uuid: str
    vertex_index: int
    original: tuple[float, float, float]
    delta: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ExplicitWeld:
    object_uuid: str
    vertex_indices: tuple[int, ...]
    original_coordinates: tuple[tuple[float, float, float], ...]
    source_polygon_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class VeyraRepairPlan:
    schema: str
    job_id: str
    source_snapshot_identity: str
    displacements: tuple[VertexDisplacement, ...]
    welds: tuple[ExplicitWeld, ...]
    attempted_count: int
    planned_count: int
    skipped_count: int
    skip_reasons: tuple[tuple[str, int], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "VeyraRepairPlan":
        if not isinstance(value, dict) or value.get("schema") != "cnx.veyra.plan.v1":
            raise ValueError("unsupported VEYRA repair-plan schema")
        allowed = set(cls.__dataclass_fields__)
        if set(value) != allowed:
            raise ValueError("invalid VEYRA repair-plan fields")
        displacements = tuple(VertexDisplacement(
            object_uuid=str(item["object_uuid"]),
            vertex_index=int(item["vertex_index"]),
            original=_point(item["original"]), delta=_point(item["delta"]))
            for item in value["displacements"])
        welds = tuple(ExplicitWeld(
            object_uuid=str(item["object_uuid"]),
            vertex_indices=tuple(int(index) for index in item["vertex_indices"]),
            original_coordinates=tuple(_point(point) for point in
                                       item["original_coordinates"]),
            source_polygon_indices=tuple(int(index) for index in
                                         item["source_polygon_indices"]))
            for item in value["welds"])
        result = cls(
            schema=str(value["schema"]), job_id=str(value["job_id"]),
            source_snapshot_identity=str(value["source_snapshot_identity"]),
            displacements=displacements, welds=welds,
            attempted_count=int(value["attempted_count"]),
            planned_count=int(value["planned_count"]),
            skipped_count=int(value["skipped_count"]),
            skip_reasons=tuple((str(key), int(count)) for key, count in
                               value["skip_reasons"]))
        if (not result.job_id or len(result.source_snapshot_identity) != 64
                or min(result.attempted_count, result.planned_count,
                       result.skipped_count) < 0
                or result.planned_count + result.skipped_count !=
                    result.attempted_count):
            raise ValueError("invalid VEYRA repair-plan counts or identity")
        return result


def _point(value) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("VEYRA point must have three components")
    result = tuple(float(component) for component in value)
    if not all(abs(component) < float("inf") for component in result):
        raise ValueError("VEYRA point must be finite")
    return result


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def identity_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
