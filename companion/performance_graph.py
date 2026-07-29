"""Frame-based performance history for the compact Bake companion."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math

from cloth_next.bake.status import BakeState


@dataclass
class FramePerformanceHistory:
    """Keep bounded timing data for completed solver frames."""

    limit: int = 60
    durations: deque[float] = field(init=False)
    _job_id: str = field(default="", init=False)
    _frame: int | None = field(default=None, init=False)
    _elapsed: float | None = field(default=None, init=False)
    _average_seconds: float | None = field(default=None, init=False)
    _display_low: float | None = field(default=None, init=False)
    _display_high: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.durations = deque(maxlen=max(8, int(self.limit)))

    def reset(self, job_id: str = "") -> None:
        self.durations.clear()
        self._job_id = str(job_id or "")
        self._frame = None
        self._elapsed = None
        self._average_seconds = None
        self._display_low = None
        self._display_high = None

    def observe(self, snapshot) -> bool:
        job_id = str(getattr(snapshot, "job_id", "") or "")
        if job_id != self._job_id:
            self.reset(job_id)
        if getattr(snapshot, "state", None) is not BakeState.SIMULATING:
            return False
        frame = getattr(snapshot, "current_frame", None)
        elapsed = getattr(snapshot, "elapsed_seconds", None)
        if frame is None or elapsed is None:
            return False
        frame, elapsed = int(frame), float(elapsed)
        if not math.isfinite(elapsed):
            return False
        if self._frame is None or self._elapsed is None:
            self._frame, self._elapsed = frame, elapsed
            return False
        frame_delta, elapsed_delta = frame - self._frame, elapsed - self._elapsed
        self._frame, self._elapsed = frame, elapsed
        if frame_delta <= 0 or elapsed_delta <= 0.0:
            return False
        current = elapsed_delta / frame_delta
        for _ in range(min(frame_delta, self.durations.maxlen)):
            self.durations.append(current)
        self._average_seconds = (
            current if self._average_seconds is None
            else self._average_seconds*.7+current*.3)
        self._update_display_range()
        return True

    @property
    def latest_seconds(self) -> float | None:
        return self.durations[-1] if self.durations else None

    @property
    def average_seconds(self) -> float | None:
        return self._average_seconds

    @property
    def display_range(self) -> tuple[float,float] | None:
        if self._display_low is None or self._display_high is None:
            return None
        return self._display_low,self._display_high

    def _update_display_range(self) -> None:
        values=tuple(self.durations)
        if not values:return
        low,high=min(values),max(values)
        span=max(high-low,(self._average_seconds or high)*.1,.1)
        target_low=max(0.,low-span*.25)
        target_high=high+span*.25
        if self._display_low is None or self._display_high is None:
            self._display_low,self._display_high=target_low,target_high
            return
        # Expand immediately for outliers, contract gradually to avoid jitter.
        self._display_low=min(target_low,self._display_low*.85+target_low*.15)
        self._display_high=max(target_high,self._display_high*.85+target_high*.15)

