# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable status snapshots shared by every Cloth NeXt bake interface."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
import json
import math
import time
from typing import Any


class BakeState(str, Enum):
    IDLE = "IDLE"
    PREPARING = "PREPARING"
    EXPORTING = "EXPORTING"
    STARTING_SOLVER = "STARTING_SOLVER"
    UPLOADING = "UPLOADING"
    BUILDING = "BUILDING"
    SIMULATING = "SIMULATING"
    FETCHING = "FETCHING"
    IMPORTING = "IMPORTING"
    FINISHED = "FINISHED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


_TITLES = {s: s.value.replace("_", " ").title() for s in BakeState}
_ACTIVE = {BakeState.PREPARING, BakeState.EXPORTING,
           BakeState.STARTING_SOLVER, BakeState.UPLOADING,
           BakeState.BUILDING, BakeState.SIMULATING, BakeState.FETCHING,
           BakeState.IMPORTING, BakeState.CANCELLING}


def format_duration(seconds: float | None, *, approximate: bool = False) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "Unknown"
    value = int(seconds)
    hours, value = divmod(value, 3600)
    minutes, secs = divmod(value, 60)
    text = f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
    return f"~{text}" if approximate else text


@dataclass(frozen=True, slots=True)
class BakeSnapshot:
    state: BakeState = BakeState.IDLE
    progress_current: int = 0
    progress_total: int | None = None
    current_frame: int | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    status_title: str = "Idle"
    status_message: str = "Ready"
    active_object_name: str = ""
    elapsed_seconds: float = 0.0
    estimated_remaining_seconds: float | None = None
    can_cancel: bool = False
    can_pause: bool = False
    is_paused: bool = False
    error_summary: str = ""
    error_details: str = ""
    job_id: str = ""
    updated_at: float = 0.0
    preview: bool = False

    @property
    def progress_fraction(self) -> float:
        if self.progress_total is None or self.progress_total <= 0:
            return 0.0
        return min(1.0, max(0.0, self.progress_current / self.progress_total))

    @property
    def active(self) -> bool:
        return self.state in _ACTIVE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        data["progress_fraction"] = self.progress_fraction
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BakeSnapshot":
        allowed = {field for field in cls.__dataclass_fields__}
        values = {key: value for key, value in data.items() if key in allowed}
        values["state"] = BakeState(values.get("state", "IDLE"))
        return cls(**values)

    @classmethod
    def from_json(cls, value: str) -> "BakeSnapshot":
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("status message must be a JSON object")
        return cls.from_dict(data)


def normalized(snapshot: BakeSnapshot, **changes: Any) -> BakeSnapshot:
    state = changes.get("state", snapshot.state)
    changes.setdefault("status_title", _TITLES[state])
    changes.setdefault("can_cancel", state in _ACTIVE - {BakeState.CANCELLING})
    changes.setdefault("updated_at", time.time())
    current = max(0, int(changes.get("progress_current", snapshot.progress_current)))
    total = changes.get("progress_total", snapshot.progress_total)
    if total is not None:
        total = max(0, int(total))
        if total > 0:
            current = min(current, total)
    changes["progress_current"] = current
    changes["progress_total"] = total
    return replace(snapshot, **changes)
