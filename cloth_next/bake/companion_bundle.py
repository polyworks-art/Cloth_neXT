# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict validation of the one allowed bundled companion executable."""
from __future__ import annotations
import hashlib
import json
import os
import stat
from pathlib import Path
from ..platform_support import PlatformSpec, platform_spec

FILENAME="cloth-next-bake.exe"  # Backwards-compatible Windows API constant.
def validate_bundle(extension_root: Path, expected_version: str, *,
                    platform: PlatformSpec | None = None) -> Path:
    expected = platform or platform_spec()
    manifest=extension_root/"companion_manifest.json"
    payload=json.loads(manifest.read_text("utf-8"))
    required={"schema_version","cloth_next_version","filename","platform","file_size","sha256","modes"}
    if set(payload)!=required or payload["schema_version"]!=2: raise ValueError("invalid companion manifest schema")
    if payload["cloth_next_version"]!=expected_version: raise ValueError("companion version mismatch")
    if (payload["filename"] != expected.companion_filename
            or payload["platform"] != expected.blender_platform):
        raise ValueError("invalid companion identity")
    if payload["modes"] != ["bake","veyra","welcome","whats-new"]:
        raise ValueError("bundled companion does not support every required mode")
    binary=extension_root/"bin"/expected.companion_filename
    data=binary.read_bytes()
    if len(data)!=payload["file_size"]: raise ValueError("companion size mismatch")
    if hashlib.sha256(data).hexdigest()!=payload["sha256"]: raise ValueError("companion hash mismatch")
    if (expected.executable_permission_required and os.name == "posix"
            and not binary.stat().st_mode & 0o111):
        # Blender's extension installer currently strips executable mode bits
        # while unpacking ZIPs. Restore them only after the exact binary has
        # passed the signed-in-repository manifest identity, size, and hash
        # checks above; never chmod an unauthenticated path.
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP |
                     stat.S_IXOTH)
        if not binary.stat().st_mode & 0o111:
            raise ValueError("companion executable permission is missing")
    return binary
