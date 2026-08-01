# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded deterministic rewind-snapshot selection."""

from __future__ import annotations

from collections import OrderedDict


class SnapshotStore:
    def __init__(self, maximum: int):
        if maximum < 2:
            raise ValueError("at least two Newton snapshots are required")
        self.maximum = int(maximum)
        self._items = OrderedDict()

    def put(self, frame: int, value) -> None:
        frame = int(frame)
        self._items[frame] = value
        self._items.move_to_end(frame)
        while len(self._items) > self.maximum:
            removable = next((key for key in self._items if key != min(self._items)), None)
            if removable is None:
                break
            del self._items[removable]

    def nearest_at_or_before(self, frame: int):
        candidates = [key for key in self._items if key <= int(frame)]
        if not candidates:
            return None
        key = max(candidates)
        return key, self._items[key]

    def clear_except_initial(self) -> None:
        if not self._items:
            return
        first = min(self._items)
        value = self._items[first]
        self._items.clear()
        self._items[first] = value

    def __len__(self):
        return len(self._items)
