# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Solver frames -> published PC2 caches with sidecars (pure, no bpy).

This is the single import path for solver results. The local Bake worker
feeds it frames while the solver runs; the Cloud result import feeds it the
same frames read from downloaded raw solver output. Both therefore share the
Sewing correction, the ``(Z2Y @ world)^-1`` transform, the streaming PC2
writer and the sidecar generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Mapping

import numpy as np

from . import cache_metadata, pc2
from ..ppf.coordinates import solver_world_to_object_local, transform_points_numpy

# PC2 sample 0 = Blender frame 1 = exported initial state (see
# ppf_run/import_result.py, which documents the frame mapping).
PC2_START_FRAME = 0.0
PC2_SAMPLE_RATE = 1.0


def snap_closed_sewing_pairs(positions, pairs, threshold: float):
    """Close solved seams exactly once PPF has pulled a pair into range."""
    if not pairs or threshold <= 0.0:
        return positions
    result = np.asarray(positions).copy()
    baseline = np.asarray(positions)
    for first, second in pairs:
        if np.linalg.norm(baseline[first] - baseline[second]) < threshold:
            midpoint = 0.5 * (baseline[first] + baseline[second])
            result[first] = midpoint
            result[second] = midpoint
    return result


@dataclass(frozen=True, slots=True)
class ImportTarget:
    """Everything one object's cache needs; values fixed at export time."""

    uuid: str
    object_name: str
    pc2_path: Path
    initial_local: Any            # (vertex_count, 3) Blender object-local
    world_matrix: Any             # export-time Blender world matrix (4x4)
    stitch_pairs: tuple[tuple[int, int], ...] = ()
    stitch_snap_distance: float = 0.0
    material_meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def vertex_count(self) -> int:
        return len(self.initial_local)


class MissingObjectResult(ValueError):
    pass


class PlaybackCacheImport:
    """Write one PC2 per target, frame by frame, publish only complete ones.

    ``frame_count`` counts PC2 samples (Blender frames), including sample 0.
    """

    def __init__(self, targets, *, frame_count: int,
                 recovery_partials: Mapping[str, str] | None = None) -> None:
        self.targets = tuple(targets)
        self.frame_count = int(frame_count)
        self._recovery_partials = dict(recovery_partials or {})
        self.writers: dict[str, pc2.StreamingPc2Writer] = {}
        self.partials: dict[str, dict] = {}
        self.transform_seconds = 0.0
        self.write_seconds = 0.0
        self._to_local = {}

    def begin(self) -> None:
        """Install fail-closed partial sidecars before any cache exists."""
        for target in self.targets:
            meta = target.material_meta
            if meta:
                partial = cache_metadata.partial_metadata(
                    cache_path=target.pc2_path,
                    fingerprints=meta["fingerprints"],
                    identities=meta["identities"],
                    expected=meta["expected"],
                    details=meta["details"])
                cache_metadata.write_atomic(
                    cache_metadata.sidecar_path(target.pc2_path), partial)
                self.partials[target.uuid] = partial
        self._to_local = {target.uuid: solver_world_to_object_local(
            target.world_matrix) for target in self.targets}

    def ensure_writers(self) -> None:
        if self.writers:
            return
        step = time.monotonic()
        for target in self.targets:
            writer = pc2.StreamingPc2Writer(
                target.pc2_path,
                vertex_count=target.vertex_count,
                frame_count=self.frame_count,
                start_frame=PC2_START_FRAME,
                sample_rate=PC2_SAMPLE_RATE,
                resume_path=(
                    Path(self._recovery_partials[target.uuid])
                    if target.uuid in self._recovery_partials else None))
            if writer.frames_written == 0:
                writer.write_frame(target.initial_local)
            self.writers[target.uuid] = writer
        self.write_seconds += time.monotonic() - step

    def write_frame(self, positions_by_uuid: Mapping[str, Any]) -> None:
        """Append one solver frame (solver world space) to every cache."""
        for target in self.targets:
            positions = positions_by_uuid.get(target.uuid)
            if positions is None:
                raise MissingObjectResult(
                    f"solver frame has no result for {target.object_name}")
            positions = snap_closed_sewing_pairs(
                positions, target.stitch_pairs, target.stitch_snap_distance)
            step = time.monotonic()
            local = transform_points_numpy(self._to_local[target.uuid],
                                           positions)
            self.transform_seconds += time.monotonic() - step
            step = time.monotonic()
            self.writers[target.uuid].write_frame(local)
            self.write_seconds += time.monotonic() - step

    def live_paths(self) -> dict[str, str]:
        return {target.uuid: str(self.writers[target.uuid].expose_live())
                for target in self.targets}

    def finalize(self, *, solver_identity: Mapping[str, Any] | None = None,
                 contacts: Mapping[str, Any] | None = None,
                 timings: Mapping[str, float] | None = None,
                 execution: Mapping[str, Any] | None = None) \
            -> dict[str, pc2.Pc2Header]:
        """Publish every cache, then its complete sidecar.

        ``execution`` is an optional, additive sidecar field (Cloud Bakes).
        Local Bakes never write it, so their sidecars stay byte-compatible.
        """
        headers = {}
        for target in self.targets:
            writer = self.writers[target.uuid]
            headers[target.uuid] = writer.finalize()
            partial = self.partials.get(target.uuid)
            if partial is None:
                continue
            if solver_identity:
                identities = dict(partial["identities"])
                merged = dict(identities.get("solver", {}))
                merged.update(solver_identity)
                identities["solver"] = merged
                partial["identities"] = identities
            if contacts:
                details = dict(partial.get("details", {}))
                details["contacts"] = dict(contacts)
                partial["details"] = details
            if execution:
                partial["execution"] = dict(execution)
            metadata = cache_metadata.completed_metadata(
                partial, cache_path=target.pc2_path, timings=timings)
            cache_metadata.write_atomic(
                cache_metadata.sidecar_path(target.pc2_path), metadata)
        return headers

    def abort(self) -> None:
        for writer in self.writers.values():
            writer.abort()
