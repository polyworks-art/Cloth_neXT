# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure beta-readiness helpers for preflight, support, and cache recovery."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import os

from ..bake import cache_metadata


class HealthSeverity(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class HealthCheck:
    key: str
    severity: HealthSeverity
    title: str
    detail: str
    action: str = ""


@dataclass(frozen=True, slots=True)
class CacheEntry:
    cache_path: Path
    metadata_path: Path
    condition: str
    message: str
    size_bytes: int
    deletable: bool


def pc2_size_bytes(vertex_count: int, frame_count: int) -> int:
    """Conservative POINTCACHE2 size estimate including its fixed header."""
    return 32 + max(0, int(vertex_count)) * max(0, int(frame_count)) * 12


def collider_capture_bytes(vertex_count: int, frame_count: int,
                           samples_per_frame: int) -> int:
    samples = max(1, (max(1, int(frame_count)) - 1)
                  * max(2, int(samples_per_frame)) + 1)
    return max(0, int(vertex_count)) * samples * 12


def human_bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024.0 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024.0
    return f"{amount:.1f} TB"


def inventory_cache(root: Path) -> tuple[CacheEntry, ...]:
    """Inventory only Cloth NeXt-owned cache names directly below ``root``."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        return ()
    paths: set[Path] = set(root.glob("cn_test_cloth_*.pc2"))
    for sidecar in root.glob("cn_test_cloth_*.meta.json"):
        paths.add(sidecar.with_suffix("").with_suffix(".pc2"))
    entries = []
    for path in sorted(paths, key=lambda item: item.name.lower()):
        inspection = cache_metadata.inspect_cache(path)
        condition = inspection.condition.value
        pc2_exists = path.is_file()
        meta = cache_metadata.sidecar_path(path)
        size = ((path.stat().st_size if pc2_exists else 0)
                + (meta.stat().st_size if meta.is_file() else 0))
        # Missing metadata can be a legacy user cache. Never bulk-delete it.
        deletable = condition in {"PARTIAL", "CORRUPT"} and meta.is_file()
        entries.append(CacheEntry(path, meta, condition, inspection.message,
                                  size, deletable))
    return tuple(entries)


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def remove_invalid(entries: tuple[CacheEntry, ...], root: Path) -> tuple[Path, ...]:
    """Remove authenticated invalid pairs, fail-closed outside the cache root."""
    root = Path(root).expanduser().resolve()
    removed = []
    for entry in entries:
        if not entry.deletable:
            continue
        for path in (entry.cache_path, entry.metadata_path):
            if (not _contained(path, root)
                    or not path.name.startswith("cn_test_cloth_")
                    or path.suffix.lower() not in {".pc2", ".json"}):
                raise ValueError(f"refusing unsafe cache cleanup path: {path}")
            if path.exists():
                path.unlink()
                removed.append(path)
    return tuple(removed)


def redact_text(value: object, replacements: dict[str, str]) -> str:
    text = str(value or "")
    ordered = sorted(((str(key), replacement)
                      for key, replacement in replacements.items() if key),
                     key=lambda pair: len(pair[0]), reverse=True)
    for sensitive, replacement in ordered:
        text = text.replace(sensitive, replacement)
        text = text.replace(sensitive.replace("\\", "/"), replacement)
    return text[:32_000]


def support_markdown(sections: tuple[tuple[str, tuple[tuple[str, object], ...]], ...]) -> str:
    lines = ["# Cloth NeXt Support Report", "",
             "This report contains no mesh geometry or file contents.", ""]
    for title, rows in sections:
        lines.extend((f"## {title}", ""))
        for label, value in rows:
            lines.append(f"- {label}: {value}")
        lines.append("")
    return "\n".join(lines)
