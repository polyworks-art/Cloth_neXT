# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Graded activation of a stored Cloud result (concept 10.3, E9).

Pure decision logic: the Blender adapter gathers the current values once
(on an explicit action, never while drawing) and this module decides.

``READY``          nothing relevant changed: may activate automatically
``STALE_SETTINGS`` simulation parameters (incl. transforms) changed: ask
``STALE_GEOMETRY`` same topology, different shape: ask
``INCOMPATIBLE``   topology / vertex order / count changed or object
                   missing: never attach; keep the result stored
``NEWER_LOCAL``    a newer valid local Bake exists: never overwrite
                   automatically; offer manual activation
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence


class Verdict(str, Enum):
    READY = "READY"
    STALE_SETTINGS = "STALE_SETTINGS"
    STALE_GEOMETRY = "STALE_GEOMETRY"
    NEWER_LOCAL = "NEWER_LOCAL"
    INCOMPATIBLE = "INCOMPATIBLE"


_SEVERITY = {Verdict.READY: 0, Verdict.STALE_SETTINGS: 1,
             Verdict.STALE_GEOMETRY: 2, Verdict.NEWER_LOCAL: 3,
             Verdict.INCOMPATIBLE: 4}


@dataclass(frozen=True, slots=True)
class CurrentObject:
    """What the open scene says about one snapshot object right now."""

    present: bool
    vertex_count: int = 0
    topology_signature: str = ""
    local_result_time: float | None = None   # completed local PC2, if any


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Verdict
    per_object: tuple[tuple[str, Verdict, str], ...]

    @property
    def may_auto_activate(self) -> bool:
        return self.verdict is Verdict.READY

    @property
    def may_activate(self) -> bool:
        return self.verdict is not Verdict.INCOMPATIBLE


def classify(snapshot_objects: Sequence[Mapping], current: Mapping[str, CurrentObject],
             *, settings_fingerprint: str | None, geometry_fingerprint: str | None,
             submitted_at: float) -> Decision:
    rows = []
    for item in snapshot_objects:
        uuid = item["uuid"]
        name = item["object_name"]
        now = current.get(uuid, CurrentObject(False))
        fingerprints = (item.get("material_meta") or {}).get("fingerprints", {})
        if not now.present:
            rows.append((uuid, Verdict.INCOMPATIBLE, f"{name}: object not found"))
        elif (now.vertex_count != item["vertex_count"]
              or now.topology_signature != item["topology_signature"]):
            rows.append((uuid, Verdict.INCOMPATIBLE,
                         f"{name}: mesh topology or vertex order changed"))
        elif now.local_result_time is not None and now.local_result_time > submitted_at:
            rows.append((uuid, Verdict.NEWER_LOCAL,
                         f"{name}: a newer local Bake exists"))
        elif (geometry_fingerprint is not None
              and fingerprints.get("geometry") not in (None, geometry_fingerprint)):
            rows.append((uuid, Verdict.STALE_GEOMETRY, f"{name}: geometry changed"))
        elif (settings_fingerprint is not None
              and fingerprints.get("settings") not in (None, settings_fingerprint)):
            rows.append((uuid, Verdict.STALE_SETTINGS, f"{name}: settings changed"))
        else:
            rows.append((uuid, Verdict.READY, f"{name}: up to date"))
    worst = max((row[1] for row in rows), key=_SEVERITY.__getitem__,
                default=Verdict.INCOMPATIBLE)
    return Decision(worst, tuple(rows))
