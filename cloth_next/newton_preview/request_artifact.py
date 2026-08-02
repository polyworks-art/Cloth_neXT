# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic, bounded scene-request artifacts for the local Newton worker."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid

MAX_REQUEST_ARTIFACT_BYTES = 1024 * 1024 * 1024


def write_request_artifact(result_directory, request_wire: dict) -> dict:
    root = Path(result_directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    final = root / "create_preview_request.json"
    temporary = root / f".{final.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            json.dump(request_wire, stream, separators=(",", ":"),
                      allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        size = temporary.stat().st_size
        if size <= 0 or size > MAX_REQUEST_ARTIFACT_BYTES:
            raise ValueError("Newton scene request artifact exceeds its bounded size")
        digest = hashlib.sha256()
        with temporary.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        os.replace(temporary, final)
        return {"path": str(final), "size": size,
                "sha256": digest.hexdigest()}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def read_request_artifact(metadata: dict, result_directory) -> dict:
    root = Path(result_directory).resolve()
    path = Path(str(metadata["path"])).resolve()
    if path.parent != root or path.name != "create_preview_request.json":
        raise ValueError("Newton scene request artifact is outside its owned session")
    expected_size = int(metadata["size"])
    if (expected_size <= 0 or expected_size > MAX_REQUEST_ARTIFACT_BYTES
            or not path.is_file() or path.stat().st_size != expected_size):
        raise ValueError("Newton scene request artifact size is invalid")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != str(metadata["sha256"]):
        raise ValueError("Newton scene request artifact checksum mismatch")
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Newton scene request artifact must contain an object")
    return value
