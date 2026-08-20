# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic, bounded and hash-verified VEYRA session artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .model import RepairArtifact, canonical_json

MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


class SessionArtifacts:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, relative_path: str) -> Path:
        if (not relative_path or any(char not in _NAME_CHARS
                                     for char in relative_path)
                or relative_path in {".", ".."}):
            raise ValueError("invalid VEYRA artifact name")
        path = (self.root / relative_path).resolve(strict=False)
        if path.parent != self.root:
            raise ValueError("VEYRA artifact escapes the session root")
        return path

    def write_json(self, *, schema: str, job_id: str, name: str,
                   value) -> RepairArtifact:
        payload = canonical_json(value)
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise ValueError("VEYRA artifact exceeds the size limit")
        path = self._path(name)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        return RepairArtifact(schema, job_id, name, len(payload),
                              hashlib.sha256(payload).hexdigest())

    def read_json(self, artifact: RepairArtifact, *, schema: str,
                  job_id: str):
        if artifact.schema != schema or artifact.job_id != job_id:
            raise ValueError("stale or incompatible VEYRA artifact")
        if artifact.size < 0 or artifact.size > MAX_ARTIFACT_BYTES:
            raise ValueError("invalid VEYRA artifact size")
        path = self._path(artifact.relative_path)
        data = path.read_bytes()
        if len(data) != artifact.size:
            raise ValueError("VEYRA artifact size mismatch")
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ValueError("VEYRA artifact digest mismatch")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("malformed VEYRA artifact") from exc
