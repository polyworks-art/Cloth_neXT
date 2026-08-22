# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure monotonic candidate transaction used to regression-test orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, MutableMapping, Sequence


Point = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class IterationCandidate:
    candidate_id: str
    deltas: Mapping[int, Point]


@dataclass(frozen=True, slots=True)
class IterationRecord:
    candidate_id: str
    before: int
    after: int
    accepted: bool


@dataclass(frozen=True, slots=True)
class IterationResult:
    initial_count: int
    final_count: int
    accepted: int
    rejected: int
    records: tuple[IterationRecord, ...]
    termination_reason: str


def run_monotonic_iterations(
        coordinates: MutableMapping[int, Point], initial_count: int,
        candidates: Callable[[int, int], Sequence[IterationCandidate]],
        validate: Callable[[Mapping[int, Point], IterationCandidate], int], *,
        cancelled: Callable[[], bool] | None = None,
        maximum_iterations: int = 8) -> IterationResult:
    """Apply, validate and accept/rollback candidates using exact snapshots."""
    baseline = int(initial_count); initial = baseline
    accepted = 0; rejected = 0; records = []
    for pass_index in range(maximum_iterations):
        if cancelled and cancelled():
            return IterationResult(initial, baseline, accepted, rejected,
                                   tuple(records), "CANCELLED")
        batch = tuple(candidates(baseline, pass_index))
        if not batch:
            reason = ("PARTIAL_SUCCESS" if accepted else "NO_SAFE_REPAIR")
            return IterationResult(initial, baseline, accepted, rejected,
                                   tuple(records), reason)
        improved = False
        for candidate in batch:
            saved = {index: coordinates[index]
                     for index in candidate.deltas}
            for index, delta in candidate.deltas.items():
                coordinates[index] = tuple(
                    saved[index][axis] + delta[axis] for axis in range(3))
            if cancelled and cancelled():
                coordinates.update(saved)
                return IterationResult(initial, baseline, accepted, rejected,
                                       tuple(records), "CANCELLED")
            after = int(validate(coordinates, candidate))
            keep = after < baseline
            records.append(IterationRecord(
                candidate.candidate_id, baseline, after, keep))
            if keep:
                baseline = after; accepted += 1; improved = True
                break
            coordinates.update(saved)
            rejected += 1
        if baseline == 0:
            return IterationResult(initial, 0, accepted, rejected,
                                   tuple(records), "SUCCESS")
        if not improved:
            reason = ("PARTIAL_SUCCESS" if accepted else "NO_SAFE_REPAIR")
            return IterationResult(initial, baseline, accepted, rejected,
                                   tuple(records), reason)
    return IterationResult(
        initial, baseline, accepted, rejected, tuple(records),
        "PARTIAL_SUCCESS_ITERATION_CAP" if accepted else "NO_SAFE_REPAIR")
