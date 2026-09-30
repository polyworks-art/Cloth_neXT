# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure helpers for persistent, path-based Sewing definitions."""

from __future__ import annotations

from dataclasses import dataclass

from .attachments import AttachmentError, ordered_path_mapping


class SewingError(AttachmentError):
    """A Sewing definition is invalid or cannot be exported safely."""


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
    strength: float = 1.0

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


def interaction_visible(master, item_overlay, *, playback=False, baking=False):
    """Central visibility rule shared by drawing and viewport interaction."""
    return bool(master and item_overlay and not playback and not baking)


def merge_stitch_pairs(legacy, explicit):
    """Deduplicate old loose-edge stitches and new explicit intra-object pairs."""
    return tuple(sorted({tuple(sorted(map(int, pair)))
                         for pair in (*tuple(legacy), *tuple(explicit))
                         if int(pair[0]) != int(pair[1])}))
