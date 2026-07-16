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
    STARTING_COMPANION = "STARTING_COMPANION"
    WAITING_FOR_COMPANION = "WAITING_FOR_COMPANION"
    COMPANION_READY = "COMPANION_READY"
    STARTING_RUN = "STARTING_RUN"
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

class BakeJobKind(str, Enum):
    PREVIEW = "PREVIEW"
    SOLVER_TEST = "SOLVER_TEST"
    BAKE = "BAKE"

class BakeActivity(str, Enum):
    IDLE="IDLE"; VALIDATING="VALIDATING"; CAPTURING_GEOMETRY="CAPTURING_GEOMETRY"
    ENCODING_SCENE="ENCODING_SCENE"; STARTING_SOLVER="STARTING_SOLVER"
    BUILDING_CONTACTS="BUILDING_CONTACTS"; DETECTING_COLLISIONS="DETECTING_COLLISIONS"
    SOLVING_CONSTRAINTS="SOLVING_CONSTRAINTS"; UPDATING_PINS="UPDATING_PINS"
    ADVANCING_SIMULATION="ADVANCING_SIMULATION"; WRITING_FRAME="WRITING_FRAME"
    WAITING_FOR_OUTPUT="WAITING_FOR_OUTPUT"; READING_RESULTS="READING_RESULTS"
    BUILDING_PC2="BUILDING_PC2"; APPLYING_PLAYBACK="APPLYING_PLAYBACK"
    CLEANING_UP="CLEANING_UP"; CANCELLING="CANCELLING"; FINISHED="FINISHED"
    ERROR="ERROR"; UNKNOWN="UNKNOWN"
    CAPTURING_PIN_TARGETS="CAPTURING_PIN_TARGETS"
    VALIDATING_PIN_TOPOLOGY="VALIDATING_PIN_TOPOLOGY"
    ENCODING_PIN_ANIMATION="ENCODING_PIN_ANIMATION"

ACTIVITY_LABELS = {
    BakeActivity.IDLE:"Waiting for a Bake", BakeActivity.VALIDATING:"Validating Blender scene",
    BakeActivity.CAPTURING_GEOMETRY:"Preparing evaluated geometry",
    BakeActivity.ENCODING_SCENE:"Encoding PPF scene", BakeActivity.STARTING_SOLVER:"Initializing solver",
    BakeActivity.BUILDING_CONTACTS:"Building contact constraints",
    BakeActivity.DETECTING_COLLISIONS:"Detecting collision candidates",
    BakeActivity.SOLVING_CONSTRAINTS:"Solving constraints", BakeActivity.UPDATING_PINS:"Updating pinned vertices",
    BakeActivity.ADVANCING_SIMULATION:"Advancing simulation", BakeActivity.WRITING_FRAME:"Writing frame",
    BakeActivity.WAITING_FOR_OUTPUT:"Waiting for solver output", BakeActivity.READING_RESULTS:"Reading simulated vertices",
    BakeActivity.BUILDING_PC2:"Building PC2 cache", BakeActivity.APPLYING_PLAYBACK:"Applying playback cache",
    BakeActivity.CLEANING_UP:"Cleaning temporary files", BakeActivity.CANCELLING:"Cancelling solver",
    BakeActivity.FINISHED:"Playback cache ready", BakeActivity.ERROR:"Solver activity failed",
    BakeActivity.UNKNOWN:"Running solver",
    BakeActivity.CAPTURING_PIN_TARGETS:"Capturing animated Pin targets",
    BakeActivity.VALIDATING_PIN_TOPOLOGY:"Validating Pin topology",
    BakeActivity.ENCODING_PIN_ANIMATION:"Encoding Pin animation",
}

PHASE_ACTIVITIES = {"PREPARING":BakeActivity.CAPTURING_GEOMETRY, "EXPORTING":BakeActivity.ENCODING_SCENE,
    "STARTING_SOLVER":BakeActivity.STARTING_SOLVER, "UPLOADING":BakeActivity.ENCODING_SCENE,
    "BUILDING":BakeActivity.UNKNOWN, "SIMULATING":BakeActivity.ADVANCING_SIMULATION,
    "FETCHING":BakeActivity.READING_RESULTS, "IMPORTING":BakeActivity.BUILDING_PC2}


_TITLES = {s: s.value.replace("_", " ").title() for s in BakeState}
_ACTIVE = {BakeState.PREPARING, BakeState.STARTING_COMPANION,
           BakeState.WAITING_FOR_COMPANION, BakeState.COMPANION_READY,
           BakeState.STARTING_RUN, BakeState.EXPORTING,
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
    error_code: str = ""
    job_id: str = ""
    updated_at: float = 0.0
    preview: bool = False
    job_kind: BakeJobKind = BakeJobKind.BAKE
    solver_mode: str = ""
    solver_version: str = ""
    solver_process_id: int | None = None
    activity_code: BakeActivity = BakeActivity.IDLE
    activity_label: str = ""
    activity_detail: str = ""

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
        data["job_kind"] = self.job_kind.value
        data["activity_code"] = self.activity_code.value
        data["progress_fraction"] = self.progress_fraction
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BakeSnapshot":
        allowed = {field for field in cls.__dataclass_fields__}
        values = {key: value for key, value in data.items() if key in allowed}
        values["state"] = BakeState(values.get("state", "IDLE"))
        values["job_kind"] = BakeJobKind(values.get("job_kind", "BAKE"))
        try: values["activity_code"] = BakeActivity(values.get("activity_code", "IDLE"))
        except ValueError: values["activity_code"] = BakeActivity.UNKNOWN
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
    if "activity_code" not in changes and state != snapshot.state:
        changes["activity_code"] = PHASE_ACTIVITIES.get(state.value, {
            BakeState.IDLE:BakeActivity.IDLE, BakeState.CANCELLING:BakeActivity.CANCELLING,
            BakeState.CANCELLED:BakeActivity.CLEANING_UP, BakeState.FINISHED:BakeActivity.FINISHED,
            BakeState.ERROR:BakeActivity.ERROR}.get(state, BakeActivity.UNKNOWN))
    if changes.get("preview", snapshot.preview):
        changes.setdefault("job_kind", BakeJobKind.PREVIEW)
    current = max(0, int(changes.get("progress_current", snapshot.progress_current)))
    total = changes.get("progress_total", snapshot.progress_total)
    if total is not None:
        total = max(0, int(total))
        if total > 0:
            current = min(current, total)
    changes["progress_current"] = current
    changes["progress_total"] = total
    return replace(snapshot, **changes)
