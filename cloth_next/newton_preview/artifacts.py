# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict ownership checks for disposable Newton session artifacts."""

from __future__ import annotations

from pathlib import Path
import re
import shutil

_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")


def session_directory_from_results(result_directory: str | Path) -> Path:
    results = Path(result_directory).resolve()
    session = results.parent
    if (results.name != "results" or session.parent.name != "sessions"
            or not _SESSION_ID.fullmatch(session.name)):
        raise ValueError("path is not an owned Newton session result directory")
    return session


def remove_owned_session(result_directory: str | Path) -> None:
    session = session_directory_from_results(result_directory)
    if session.is_dir():
        shutil.rmtree(session)


def prune_owned_sessions(sessions_root: str | Path, *, keep: int = 20,
                         exclude: tuple[str, ...] = ()) -> int:
    root = Path(sessions_root).resolve()
    if root.name != "sessions" or root.parent.name != "newton":
        raise ValueError("refusing to prune outside the Newton sessions root")
    if keep < 1:
        raise ValueError("at least one Newton session must be retained")
    candidates = [item for item in root.iterdir()
                  if item.is_dir() and _SESSION_ID.fullmatch(item.name)
                  and item.name not in set(exclude)] if root.is_dir() else []
    candidates.sort(key=lambda item: (item.stat().st_mtime_ns, item.name),
                    reverse=True)
    removed = 0
    for item in candidates[keep:]:
        shutil.rmtree(item)
        removed += 1
    return removed
