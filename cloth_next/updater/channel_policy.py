# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Single source of truth for exact release-channel targets."""

from __future__ import annotations


CHANNELS = ("stable", "beta", "dev")

_ALLOWED_RELEASES = {channel: frozenset((channel,)) for channel in CHANNELS}
_PUBLICATION_TARGETS = {channel: (channel,) for channel in CHANNELS}


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
