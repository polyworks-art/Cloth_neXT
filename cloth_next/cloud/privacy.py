# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed scan of documents that leave this machine or persist.

Manifests, Import Snapshots and the job registry must never contain local
paths, the OS user name, bearer tokens or signed URLs. Builders call
``require_clean`` on their final document; a hit is a bug, not something to
redact silently.
"""

from __future__ import annotations

import getpass
import os
from pathlib import Path
import re
from typing import Any, Iterable

_WINDOWS_PATH = re.compile(r"(?:^|[\s\"'=(])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/])")
_POSIX_PATH = re.compile(
    r"(?:^|[\s\"'=(])/(?:home|Users|root|tmp|var|private|mnt|media|Volumes|opt|etc)/")
_SIGNED_URL = re.compile(
    r"[?&](?:t|token|sig|signature|X-Amz-Signature|X-Goog-Signature|se|sv)=",
    re.IGNORECASE)
_BEARER = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_SECRET_KEYS = frozenset({
    "token", "access_token", "refresh_token", "authorization", "password",
    "secret", "session", "signed_url", "upload_url", "download_url", "url"})


class PrivacyViolation(ValueError):
    pass


def _local_names(environ=None) -> tuple[str, ...]:
    environ = os.environ if environ is None else environ
    names = set()
    for key in ("USERNAME", "USER", "LOGNAME"):
        value = environ.get(key, "")
        if len(value) >= 3:
            names.add(value.lower())
    try:
        value = getpass.getuser()
        if len(value) >= 3:
            names.add(value.lower())
    except Exception:  # noqa: BLE001 - no user name is a valid answer here
        pass
    try:
        home = Path.home().name
        if len(home) >= 3:
            names.add(home.lower())
    except (RuntimeError, OSError):
        pass
    return tuple(sorted(names))


def _walk(value: Any, path: str) -> Iterable[tuple[str, Any, str | None]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}" if path else str(key))
            yield (f"{path}.{key}" if path else str(key), item, str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    else:
        yield (path, value, None)


def findings(document: Any, *, user_names: tuple[str, ...] | None = None) \
        -> list[str]:
    """Return human-readable locations of private data (empty when clean)."""
    names = _local_names() if user_names is None else user_names
    hits: list[str] = []
    for location, value, key in _walk(document, ""):
        if key is not None:
            if key.lower() in _SECRET_KEYS and value not in (None, "", {}, []):
                hits.append(f"{location}: secret-bearing field")
            continue
        if not isinstance(value, str):
            continue
        if _WINDOWS_PATH.search(value) or _POSIX_PATH.search(value):
            hits.append(f"{location}: absolute local path")
        if _SIGNED_URL.search(value):
            hits.append(f"{location}: signed URL")
        if _BEARER.search(value):
            hits.append(f"{location}: bearer token")
        lowered = value.lower()
        for name in names:
            if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", lowered):
                hits.append(f"{location}: OS user name")
                break
    return hits


def require_clean(document: Any, *, label: str,
                  user_names: tuple[str, ...] | None = None) -> None:
    hits = findings(document, user_names=user_names)
    if hits:
        raise PrivacyViolation(
            f"{label} contains private data: " + "; ".join(sorted(set(hits))[:8]))
