# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Strict Cloth NeXt version parsing, channel derivation, and ordering.

Accepts exactly the policy-supported forms (docs/RELEASE_POLICY.md section 3):

- ``STABLE.BETA.DEV`` (current numeric channel scheme)
- Legacy ``X.Y.Z-beta.N``, ``X.Y.Z-rc.N``, and ``X.Y.Z-dev.N`` forms remain
  readable for installed-build compatibility.

No other prerelease identifiers, no build metadata, no leading zeros, and
legacy prerelease numbering starts at 1. Numeric versions sort by their three
channel counters; legacy stages retain ``dev < beta < rc < plain`` ordering
within the same base version. No ``bpy`` and no third-party dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

_VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(beta|rc|dev)\.([1-9]\d*))?$")

# Legacy stage ordering within the same X.Y.Z base.
_STAGE_ORDER = {"dev": 0, "beta": 1, "rc": 2, None: 3}


@total_ordering
@dataclass(frozen=True, slots=True)
class AddonVersion:
    major: int
    minor: int
    patch: int
    stage: str | None = None  # None (numeric scheme) or a legacy stage
    stage_number: int = 0     # 0 for numeric scheme, >= 1 for legacy stages

    @property
    def is_prerelease(self) -> bool:
        return self.stage is not None

    @property
    def channel_name(self) -> str:
        """Channel encoded by STABLE.BETA.DEV, with legacy compatibility."""
        if self.stage == "dev" or (self.stage is None and self.patch > 0):
            return "dev"
        if self.stage in {"beta", "rc"} or (
                self.stage is None and self.patch == 0 and self.minor > 0):
            return "beta"
        if self.stage is None and self.major > 0 and self.minor == self.patch == 0:
            return "stable"
        raise ValueError(f"version {self} does not encode a release channel")

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
                         "(STABLE.BETA.DEV or a supported legacy prerelease)")
    major, minor, patch, stage, stage_number = match.groups()
    return AddonVersion(int(major), int(minor), int(patch), stage,
                        int(stage_number) if stage_number is not None else 0)
