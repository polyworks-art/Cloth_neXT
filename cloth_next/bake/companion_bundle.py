# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict validation of the one allowed bundled companion executable."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

FILENAME="cloth-next-bake.exe"
def validate_bundle(extension_root: Path, expected_version: str) -> Path:
    manifest=extension_root/"companion_manifest.json"
    payload=json.loads(manifest.read_text("utf-8"))
    required={"schema_version","cloth_next_version","filename","platform","file_size","sha256"}
    if set(payload)!=required or payload["schema_version"]!=1: raise ValueError("invalid companion manifest schema")
    if payload["cloth_next_version"]!=expected_version: raise ValueError("companion version mismatch")
    if payload["filename"]!=FILENAME or payload["platform"]!="windows-x64": raise ValueError("invalid companion identity")
    binary=extension_root/"bin"/FILENAME
    data=binary.read_bytes()
    if len(data)!=payload["file_size"]: raise ValueError("companion size mismatch")
    if hashlib.sha256(data).hexdigest()!=payload["sha256"]: raise ValueError("companion hash mismatch")
    return binary
