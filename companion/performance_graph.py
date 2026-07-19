"""Frame-based performance history for the compact Bake companion."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math

from cloth_next.bake.status import BakeState


@dataclass
class FramePerformanceHistory:
    """Convert solver frame timing into a bounded relative score.

    A score of 50 means the latest frame group matches the recent smoothed
    pace. Higher is faster and lower is slower. This remains meaningful across
    scenes without pretending that one absolute frame time is universally good.
    """

    limit: int = 72
    scores: deque[float] = field(init=False)
    _job_id: str = field(default="", init=False)
    _frame: int | None = field(default=None, init=False)
    _elapsed: float | None = field(default=None, init=False)
    _seconds_per_frame: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.scores = deque(maxlen=max(8, int(self.limit)))

    def reset(self, job_id: str = "") -> None:
        self.scores.clear()
        self._job_id = str(job_id or "")
        self._frame = None
        self._elapsed = None
        self._seconds_per_frame = None

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
        expected = self._seconds_per_frame or current
        score = max(0.0, min(100.0, 50.0 * expected / current))
        for _ in range(min(frame_delta, self.scores.maxlen)):
            self.scores.append(score)
        # Slow adaptation preserves visible short-term performance changes.
        self._seconds_per_frame = expected * 0.82 + current * 0.18
        return True

    @property
    def latest(self) -> int | None:
        return round(self.scores[-1]) if self.scores else None

