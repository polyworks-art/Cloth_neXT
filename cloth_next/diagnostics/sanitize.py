# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Remove personal data from diagnostic text before it is stored or sent.

Everything that leaves the computer passes :func:`scrub`: user folders,
the user and computer name, ``.blend`` file names, e-mail addresses and
anything that looks like a secret (tokens, keys, long opaque strings).
"""
from __future__ import annotations

import getpass
import os
import platform
import re
import tempfile
import unicodedata

USER = "<USER>"
FILE = "<FILE>"
HOST = "<HOST>"
SECRET = "<REDACTED>"
EMAIL = "<EMAIL>"

_BEARER = re.compile(r"(?i)\b(bearer\s+)[^\s\"',;]{6,}")
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b([\w-]*(?:token|password|passwd|secret|api[_-]?key|authorization|cookie|session)[\w-]*)"
    r"(\s*[=:]\s*|\"\s*:\s*\")([^\s\"',;&]+)")
_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|key|sig|signature|code|auth)=)[^&\s\"']+")
_OPAQUE = re.compile(r"(?<![\w/\\.-])[A-Za-z0-9_+/=-]{40,}(?![\w/\\.-])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WIN_PROFILE = re.compile(r"(?i)\b([a-z]:[\\/]+(?:users|documents and settings)[\\/]+)([^\\/:*?\"'<>|\r\n]+)")
_POSIX_PROFILE = re.compile(r"(/(?:home|Users)/)([^/\s\"':]+)")
_UNC = re.compile(r"\\\\[^\\/\s\"']+\\[^\\/\s\"']+")
_BLEND = re.compile(r"[^\\/\s\"'<>|:*?]+(\.blend\d*)\b", re.IGNORECASE)


def _known_values() -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for path, label in ((os.path.expanduser("~"), USER), (tempfile.gettempdir(), "<TEMP>")):
        if path and len(path) > 3:
            values.append((path, label))
            values.append((path.replace("\\", "/"), label))
    return values


def _names() -> list[tuple[str, str]]:
    names = []
    try:
        names.append((getpass.getuser(), USER))
    except Exception:  # noqa: BLE001 - no user name is fine
        pass
    names.append((platform.node(), HOST))
    return [(value, label) for value, label in names if value and len(value) >= 3]


def clean_text(value, limit: int) -> str:
    """Normalise line breaks, drop control characters except tab/newline, cap the length."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    text = text.strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def scrub(value, limit: int = 500) -> str:
    """Return ``value`` without personal data, cleaned and at most ``limit`` characters."""
    text = str(value or "")
    text = _BEARER.sub(lambda m: m.group(1) + SECRET, text)
    text = _ASSIGNED_SECRET.sub(lambda m: m.group(1) + m.group(2) + SECRET, text)
    text = _QUERY_SECRET.sub(lambda m: m.group(1) + SECRET, text)
    for known, label in sorted(_known_values(), key=lambda item: -len(item[0])):
        text = re.sub(re.escape(known), lambda _m, label=label: label, text, flags=re.IGNORECASE)
    text = _WIN_PROFILE.sub(lambda m: m.group(1) + USER, text)
    text = _POSIX_PROFILE.sub(lambda m: m.group(1) + USER, text)
    text = _UNC.sub("<SHARE>", text)
    text = _EMAIL.sub(EMAIL, text)
    text = _BLEND.sub(lambda m: FILE + m.group(1), text)
    text = _OPAQUE.sub(SECRET, text)
    for name, label in _names():
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])",
                      lambda _m, label=label: label, text, flags=re.IGNORECASE)
    return clean_text(text, limit)
