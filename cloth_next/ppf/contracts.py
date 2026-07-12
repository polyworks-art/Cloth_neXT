"""Project-oriented backend abstraction; no protocol implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import BackendStatusSnapshot, ProjectId, SceneTransfer


class SimulationBackend(ABC):
    @abstractmethod
    def query_compatibility(self) -> BackendStatusSnapshot: ...

    @abstractmethod
    def query_status(self, project_id: ProjectId) -> BackendStatusSnapshot: ...

    @abstractmethod
    def transfer_atomic(self, transfer: SceneTransfer) -> None: ...

    @abstractmethod
    def build(self, project_id: ProjectId, *, preserve_output: bool = False) -> None: ...

    @abstractmethod
    def start(self, project_id: ProjectId) -> None: ...

    @abstractmethod
    def resume(self, project_id: ProjectId, *, from_frame: int | None = None) -> None: ...

    @abstractmethod
    def terminate(self, project_id: ProjectId) -> None: ...

    @abstractmethod
    def save_and_quit(self, project_id: ProjectId) -> None: ...

    @abstractmethod
    def discover_complete_frames(self, project_id: ProjectId) -> tuple[int, ...]: ...

    @abstractmethod
    def fetch_frame(self, project_id: ProjectId, frame: int) -> bytes: ...

    @abstractmethod
    def cleanup_project(self, project_id: ProjectId) -> None: ...

