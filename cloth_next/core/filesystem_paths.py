# SPDX-License-Identifier: GPL-3.0-or-later
"""Separate durable path identities from Windows long-path I/O operands."""
import os
from pathlib import Path


def io_path(path: Path, *, reserved_length: int = 0) -> Path:
    path = Path(path)
    if os.name != "nt":
        return path
    value = str(path.absolute())
    if value.startswith("\\\\?\\"):
        return path
    # Preserve ordinary short-path operands for callers and instrumentation.
    # MAX_PATH's directory creation limit is lower than its file limit.
    if len(value) + reserved_length < 248:
        return path
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def resolved_path(path: Path) -> Path:
    value = str(io_path(path).resolve())
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)
