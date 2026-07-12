"""Bounded reading of verified server readiness markers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SERVER_STARTING = "SERVER_STARTING"
SERVER_READY = "SERVER_READY"


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    starting: bool
    ready: bool
    tail: tuple[str, ...]


def read_progress(path: Path, *, max_bytes: int = 64 * 1024, max_lines: int = 100) -> ProgressSnapshot:
    if not path.exists():
        return ProgressSnapshot(False, False, ())
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - max_bytes))
        raw = stream.read(max_bytes)
    lines = raw.decode("utf-8", errors="replace").splitlines()[-max_lines:]
    return ProgressSnapshot(SERVER_STARTING in lines, SERVER_READY in lines, tuple(lines))

