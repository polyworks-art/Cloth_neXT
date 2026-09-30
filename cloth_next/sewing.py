# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure helpers for persistent, path-based Sewing definitions."""

from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
import math

from .attachments import AttachmentError, ordered_path_mapping, _normalized_arc


class SewingError(AttachmentError):
    """A Sewing definition is invalid or cannot be exported safely."""


# Gaia consumes an unnormalised force factor. Its reference example uses
# 50,000 for a firm seam; the artist-facing control intentionally stays 0..100.
SEWING_STRENGTH_TO_SOLVER = 500.0


def solver_stitch_stiffness(strength: float) -> float:
    """Convert artist-facing Sewing Strength to Gaia's raw force units."""
    value = float(strength)
    if not math.isfinite(value) or value < 0.0:
        raise SewingError("Sewing Strength must be finite and cannot be negative")
    return value * SEWING_STRENGTH_TO_SOLVER


@dataclass(frozen=True, slots=True)
class SewingDefinition:
    identifier: str
    name: str
    source_uuid: str
    target_uuid: str
    side_a: tuple[int, ...]
    side_b: tuple[int, ...]
    mapping: tuple[tuple[int, int], ...]
    flipped: bool = False
    strength: float = 100.0

    @property
    def cross_object(self):
        return self.source_uuid != self.target_uuid


def path_mapping(source_vertices, side_a, target_vertices, side_b, *, flipped=None):
    """Map two ordered paths monotonically, optionally forcing orientation."""
    first, second = tuple(map(int, side_a)), tuple(map(int, side_b))
    if len(first) < 2 or len(second) < 2:
        raise SewingError("Each sewing side needs two distinct endpoints")
    if len(set(first)) != len(first) or len(set(second)) != len(second):
        raise SewingError("A sewing path may not visit a vertex twice")
    return ordered_path_mapping(source_vertices, first, target_vertices, second,
                                flipped=flipped)


def sampled_path_mapping(source_vertices, side_a, target_vertices, side_b, *, flipped=False):
    """Match rest-length coordinates to points on target edges, not vertices.

    Nearest-vertex rounding collapses adjacent source vertices when the target
    has fewer subdivisions. Barycentric edge samples keep those vertices at
    distinct positions while using the existing six-slot cross-stitch protocol.
    Each result is (source vertex, target edge start, target edge end, fraction).
    """
    first, second = tuple(map(int, side_a)), tuple(map(int, side_b))
    path_mapping(source_vertices, first, target_vertices, second, flipped=flipped)
    if flipped:
        second = tuple(reversed(second))
    source_arc = _normalized_arc(source_vertices, first)
    target_arc = _normalized_arc(target_vertices, second)
    result = []
    for index, parameter in zip(first, source_arc):
        offset = min(max(bisect_right(target_arc, parameter) - 1, 0), len(second) - 2)
        width = target_arc[offset + 1] - target_arc[offset]
        fraction = (parameter - target_arc[offset]) / width if width > 1e-12 else 0.0
        result.append((index, second[offset], second[offset + 1],
                       min(1.0, max(0.0, fraction))))
    return tuple(result)


def sampled_target_point(vertices, first, second, fraction):
    return tuple((1.0 - fraction) * float(a) + fraction * float(b)
                 for a, b in zip(vertices[first], vertices[second]))


def interaction_visible(master, item_overlay, *, playback=False, baking=False):
    """Central visibility rule shared by drawing and viewport interaction."""
    return bool(master and item_overlay and not playback and not baking)


def merge_stitch_pairs(legacy, explicit):
    """Deduplicate old loose-edge stitches and new explicit intra-object pairs."""
    return tuple(sorted({tuple(sorted(map(int, pair)))
                         for pair in (*tuple(legacy), *tuple(explicit))
                         if int(pair[0]) != int(pair[1])}))
