"""Conservative current-frame progress estimate for the Bake companion."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

from cloth_next.bake.status import BakeState


@dataclass(frozen=True, slots=True)
class FrameProgress:
    fraction: float = 0.0
    indeterminate: bool = False
    complete: bool = False


class CurrentFrameProgressEstimator:
    """Estimate visual fill without claiming solver-authoritative progress."""

    def __init__(self, *, minimum_samples=2, smoothing=0.3,
                 visual_response=0.18, clock=time.monotonic):
        self.minimum_samples = max(1, int(minimum_samples))
        self.smoothing = min(1.0, max(0.0, float(smoothing)))
        self.visual_response = max(0.01, float(visual_response))
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._identity = ("", None)
        self._frame = None
        self._frame_started_elapsed = None
        self._pending_frame = None
        self._estimated_duration = None
        self._samples = 0
        self._fraction = 0.0
        self._complete = False
        self._last_elapsed = None
        self._last_observed_at = None
        self._last_tick = None

    @property
    def estimated_duration(self) -> float | None:
        return self._estimated_duration

    @property
    def sample_count(self) -> int:
        return self._samples

    def _result(self) -> FrameProgress:
        indeterminate = (
            not self._complete
            and self._frame is not None
            and self._samples < self.minimum_samples)
        return FrameProgress(self._fraction, indeterminate, self._complete)

    def _start_frame(self, frame: int, elapsed: float, now: float) -> None:
        self._frame = frame
        self._frame_started_elapsed = elapsed
        self._pending_frame = None
        self._fraction = 0.0
        self._complete = False
        self._last_elapsed = elapsed
        self._last_observed_at = now
        self._last_tick = now

    def _record_completion(self, elapsed: float, completed_frames=1) -> None:
        if self._frame_started_elapsed is None:
            return
        duration = (
            elapsed - self._frame_started_elapsed) / max(1, completed_frames)
        if not math.isfinite(duration) or duration <= 0.0:
            return
        if self._estimated_duration is None:
            self._estimated_duration = duration
        else:
            weight = self.smoothing
            self._estimated_duration = (
                self._estimated_duration * (1.0 - weight)
                + duration * weight)
        self._samples += max(1, completed_frames)

    def observe(self, snapshot, *, now=None) -> FrameProgress:
        now = self._clock() if now is None else float(now)
        identity = (
            str(getattr(snapshot, "job_id", "") or ""),
            getattr(snapshot, "solver_process_id", None))
        if identity != self._identity:
            self.reset()
            self._identity = identity

        state = getattr(snapshot, "state", None)
        frame = getattr(snapshot, "current_frame", None)
        elapsed = getattr(snapshot, "elapsed_seconds", None)
        try:
            frame = None if frame is None else int(frame)
            elapsed = float(elapsed)
        except (TypeError, ValueError):
            frame, elapsed = None, math.nan
        if not math.isfinite(elapsed) or elapsed < 0.0:
            self.reset()
            self._identity = identity
            return self._result()

        if (self._last_elapsed is not None
                and elapsed + 1e-9 < self._last_elapsed):
            # Restart/resume counters may rewind while retaining a job id.
            self.reset()
            self._identity = identity

        if (state is BakeState.FETCHING and frame is not None
                and self._frame is not None and frame > self._frame):
            completed = frame - self._frame
            self._record_completion(elapsed, completed)
            self._pending_frame = frame
            self._fraction = 1.0
            self._complete = True
            self._last_elapsed = elapsed
            self._last_observed_at = now
            self._last_tick = now
            return self._result()

        if state is not BakeState.SIMULATING or frame is None:
            if state in {
                    BakeState.IDLE, BakeState.PREPARING,
                    BakeState.STARTING_SOLVER, BakeState.CANCELLING,
                    BakeState.CANCELLED, BakeState.ERROR,
                    BakeState.FINISHED}:
                self.reset()
                self._identity = identity
            return self._result()

        if self._frame is None:
            self._start_frame(frame, elapsed, now)
        elif self._complete and frame == self._pending_frame:
            self._start_frame(frame, elapsed, now)
        elif frame > self._frame:
            completed = frame - self._frame
            self._record_completion(elapsed, completed)
            self._start_frame(frame, elapsed, now)
        elif frame < self._frame:
            self.reset()
            self._identity = identity
            self._start_frame(frame, elapsed, now)
        else:
            self._last_elapsed = elapsed
            self._last_observed_at = now
            if self._last_tick is None:
                self._last_tick = now
        return self.tick(now=now)

    def tick(self, *, now=None) -> FrameProgress:
        now = self._clock() if now is None else float(now)
        if self._complete:
            return self._result()
        if (self._frame is None or self._estimated_duration is None
                or self._samples < self.minimum_samples
                or self._last_elapsed is None
                or self._frame_started_elapsed is None):
            return self._result()
        observed_at = (
            now if self._last_observed_at is None
            else self._last_observed_at)
        wall_extension = max(0.0, now - observed_at)
        elapsed = max(
            0.0, self._last_elapsed - self._frame_started_elapsed
            + wall_extension)
        target = min(0.95, elapsed / self._estimated_duration)
        last_tick = now if self._last_tick is None else self._last_tick
        delta = max(0.0, now - last_tick)
        response = 1.0 - math.exp(-delta / self.visual_response)
        self._fraction = max(
            self._fraction,
            min(0.95, self._fraction + (target - self._fraction) * response))
        self._last_tick = now
        return self._result()
