# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only Superhive status from Blender's native repository configuration/cache."""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True, init=False)
class SuperhiveStatus:
    connected: bool = False
    purchase_detected: bool = False

    def __init__(self, connected=False, purchase_detected=False, *, purchase_validated=None):
        # Preserve the legacy constructor keyword while making its meaning explicit.
        object.__setattr__(self, 'connected', bool(connected))
        object.__setattr__(self, 'purchase_detected', bool(
            purchase_detected if purchase_validated is None else purchase_validated))

    @property
    def purchase_validated(self):
        """Legacy detection alias; never authoritative purchase verification."""
        return self.purchase_detected


def _is_superhive(repo) -> bool:
    if not getattr(repo, "enabled", False) or not getattr(repo, "use_remote_url", True):
        return False
    try:
        address = urlsplit(getattr(repo, "remote_url", ""))
        host = (address.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return address.scheme == "https" and (host == "superhivemarket.com"
                                          or host.endswith(".superhivemarket.com"))


@lru_cache(maxsize=32)
def _cached_purchase(index: str, modified_ns: int, size: int) -> bool:
    del modified_ns, size  # Cache invalidates whenever Blender rewrites the index.
    try:
        with Path(index).open("rb") as source:
            raw = source.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            return False
        payload = json.loads(raw)
        entries = payload.get("data", [])
        return isinstance(entries, list) and any(
            isinstance(entry, dict) and entry.get("id") == "cloth_next"
            for entry in entries)
    except (OSError, ValueError, AttributeError):
        return False


def repository_status(repositories) -> SuperhiveStatus:
    connected = False
    for repo in repositories:
        if not _is_superhive(repo):
            continue
        connected = True
        directory = getattr(repo, "directory", "")
        if not directory:
            continue
        index = Path(directory) / ".blender_ext" / "index.json"
        try:
            stat = index.stat()
        except OSError:
            continue
        if _cached_purchase(str(index), stat.st_mtime_ns, stat.st_size):
            return SuperhiveStatus(True, True)
    return SuperhiveStatus(connected, False)
