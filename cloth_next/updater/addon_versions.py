# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Strict Cloth NeXt version parsing and ordering.

Accepts exactly the policy-supported forms (docs/RELEASE_POLICY.md section 3):

- ``X.Y.Z``
- ``X.Y.Z-beta.N``
- ``X.Y.Z-rc.N``

No other prerelease identifiers, no build metadata, no leading zeros, and
prerelease numbering starts at 1. Ordering: ``beta < rc < stable`` within the
same base version. No ``bpy`` and no third-party dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

_VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(beta|rc)\.([1-9]\d*))?$")

# Stable sorts above rc, which sorts above beta, for the same X.Y.Z.
_STAGE_ORDER = {"beta": 0, "rc": 1, None: 2}


@total_ordering
@dataclass(frozen=True, slots=True)
class AddonVersion:
    major: int
    minor: int
    patch: int
    stage: str | None = None  # None (stable) | "beta" | "rc"
    stage_number: int = 0     # 0 for stable, >= 1 for prereleases

    @property
    def is_prerelease(self) -> bool:
        return self.stage is not None

    def _sort_key(self) -> tuple[int, int, int, int, int]:
        return (self.major, self.minor, self.patch,
                _STAGE_ORDER[self.stage], self.stage_number)

    def __lt__(self, other: "AddonVersion") -> bool:
        if not isinstance(other, AddonVersion):
            return NotImplemented
        return self._sort_key() < other._sort_key()

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.stage is None:
            return base
        return f"{base}-{self.stage}.{self.stage_number}"


def parse_version(text: str) -> AddonVersion:
    """Parse a policy-conforming version string; anything else raises."""
    if not isinstance(text, str):
        raise ValueError(f"version must be a string, got {type(text).__name__}")
    match = _VERSION_PATTERN.match(text.strip())
    if match is None:
        raise ValueError(f"{text!r} is not a supported Cloth NeXt version "
                         "(X.Y.Z, X.Y.Z-beta.N, or X.Y.Z-rc.N)")
    major, minor, patch, stage, stage_number = match.groups()
    return AddonVersion(int(major), int(minor), int(patch), stage,
                        int(stage_number) if stage_number is not None else 0)
