# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure Dev-snapshot capability gate shared by Blender presentation code."""

from __future__ import annotations

import json
import re
from pathlib import Path

_DEV_VERSION = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.[1-9]\d*$")


def is_dev_build(package_root: Path | None = None) -> bool:
    """True only for an explicitly prepared, internally consistent Dev build."""
    root = package_root or Path(__file__).resolve().parent
    try:
        payload = json.loads((root / "dev_build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    version = payload.get("dev_version")
    return bool(payload.get("experimental") is True
                and isinstance(version, str)
                and _DEV_VERSION.fullmatch(version))
