# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Single source of truth for cumulative release-channel visibility."""

from __future__ import annotations


CHANNELS = ("stable", "beta", "dev")

# A selected repository may expose releases at its own stability level or any
# more stable level.  It still exposes exactly one active package candidate.
_ALLOWED_RELEASES = {
    "stable": frozenset(("stable",)),
    "beta": frozenset(("stable", "beta")),
    "dev": frozenset(("stable", "beta", "dev")),
}

_PUBLICATION_TARGETS = {
    "stable": ("stable", "beta", "dev"),
    "beta": ("beta", "dev"),
    "dev": ("dev",),
}


def _channel(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in CHANNELS:
        raise ValueError(f"unknown release channel {value!r}")
    return normalized


def allowed_release_channels(repository_channel: str) -> frozenset[str]:
    return _ALLOWED_RELEASES[_channel(repository_channel)]


def release_visible_in(release_channel: str,
                       repository_channel: str) -> bool:
    return _channel(release_channel) in allowed_release_channels(
        repository_channel)


def publication_targets(release_channel: str) -> tuple[str, ...]:
    return _PUBLICATION_TARGETS[_channel(release_channel)]
