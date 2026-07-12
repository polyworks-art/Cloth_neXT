# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Strongly typed, immutable backend value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


def _require_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class ProjectId:
    value: str
    def __post_init__(self) -> None: object.__setattr__(self, "value", _require_text(self.value, "ProjectId"))


@dataclass(frozen=True, slots=True)
class UploadId:
    value: str
    def __post_init__(self) -> None: object.__setattr__(self, "value", _require_text(self.value, "UploadId"))


@dataclass(frozen=True, slots=True)
class ProtocolVersion:
    value: str
    def __post_init__(self) -> None: object.__setattr__(self, "value", _require_text(self.value, "ProtocolVersion"))


@dataclass(frozen=True, slots=True)
class SchemaVersion:
    value: str
    def __post_init__(self) -> None: object.__setattr__(self, "value", _require_text(self.value, "SchemaVersion"))


class ConnectionOwnership(Enum):
    OWNED_PROCESS = auto()
    EXTERNAL_SERVER = auto()


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    can_build: bool = True
    can_resume: bool = False
    can_save_and_quit: bool = False
    can_fetch_complete_frames: bool = False
    can_cleanup_project: bool = False


@dataclass(frozen=True, slots=True)
class BackendStatusSnapshot:
    project_id: ProjectId
    protocol_version: ProtocolVersion
    schema_version: SchemaVersion
    capabilities: BackendCapabilities = field(default_factory=BackendCapabilities)
    backend_status: str = "UNKNOWN"
    complete_frames: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class SceneTransfer:
    project_id: ProjectId
    upload_id: UploadId
    scene_payload: bytes
    parameter_payload: bytes


@dataclass(frozen=True, slots=True)
class SolverConnection:
    ownership: ConnectionOwnership

    @property
    def may_terminate_process(self) -> bool:
        return self.ownership is ConnectionOwnership.OWNED_PROCESS

