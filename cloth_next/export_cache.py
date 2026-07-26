# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned, hash-verified persistent solver-input cache."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

EXPORT_CACHE_SCHEMA_VERSION = 1
SCENE_KEY_SCHEMA_VERSION = 1
PARAM_KEY_SCHEMA_VERSION = 1


def deterministic_key(kind: str, identity: dict) -> str:
    version = (SCENE_KEY_SCHEMA_VERSION if kind == "scene"
               else PARAM_KEY_SCHEMA_VERSION if kind == "param" else None)
    if version is None:
        raise ValueError(f"unsupported export cache kind: {kind}")
    blob = json.dumps(
        {"cache_schema": EXPORT_CACHE_SCHEMA_VERSION,
         "key_schema": version, "kind": kind, "identity": identity},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True, slots=True)
class CacheLookup:
    hit: bool
    path: Path | None
    digest: str = ""
    size: int = 0
    reason: str = ""
    metadata: dict | None = None


class ExportPayloadCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _paths(self, kind: str, key: str) -> tuple[Path, Path]:
        directory = self.root / kind / key[:2] / key
        return directory / f"{kind}.cbor", directory / "metadata.json"

    def lookup(self, kind: str, key: str) -> CacheLookup:
        payload, metadata_path = self._paths(kind, key)
        if not payload.is_file() or not metadata_path.is_file():
            return CacheLookup(False, None, reason="missing")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (metadata.get("cache_schema") != EXPORT_CACHE_SCHEMA_VERSION
                    or metadata.get("kind") != kind
                    or metadata.get("key") != key):
                return CacheLookup(False, None, reason="metadata mismatch")
            size = payload.stat().st_size
            if size != int(metadata["size"]):
                return CacheLookup(False, None, reason="size mismatch")
            digest = hashlib.sha256()
            with payload.open("rb") as stream:
                while chunk := stream.read(4 * 1024 * 1024):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual != metadata["sha256"]:
                return CacheLookup(False, None, reason="hash mismatch")
            return CacheLookup(
                True, payload, actual, size, "verified",
                metadata.get("plan"))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return CacheLookup(False, None, reason="corrupt metadata")

    def store(self, kind: str, key: str, source, *,
              plan: dict | None = None,
              artifacts: Mapping[str, object] | None = None) -> CacheLookup:
        payload, metadata_path = self._paths(kind, key)
        payload.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        temporary_payload = payload.with_name(f".{payload.name}.{token}.tmp")
        temporary_metadata = metadata_path.with_name(
            f".{metadata_path.name}.{token}.tmp")
        temporary_artifacts: list[Path] = []
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary_payload.open("xb") as target:
                if isinstance(source, (str, Path)):
                    with Path(source).open("rb") as stream:
                        while chunk := stream.read(4 * 1024 * 1024):
                            target.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                else:
                    view = memoryview(source)
                    target.write(view)
                    digest.update(view)
                    size = view.nbytes
                target.flush()
                os.fsync(target.fileno())
            artifact_meta = {}
            for name, artifact_source in (artifacts or {}).items():
                if (not name or Path(name).name != name
                        or name in {payload.name, metadata_path.name}):
                    raise ValueError(f"invalid cache artifact name: {name!r}")
                artifact_target = payload.parent / name
                artifact_temporary = artifact_target.with_name(
                    f".{name}.{token}.tmp")
                temporary_artifacts.append(artifact_temporary)
                artifact_digest = hashlib.sha256()
                artifact_size = 0
                with artifact_temporary.open("xb") as target:
                    if isinstance(artifact_source, (str, Path)):
                        with Path(artifact_source).open("rb") as stream:
                            while chunk := stream.read(4 * 1024 * 1024):
                                target.write(chunk)
                                artifact_digest.update(chunk)
                                artifact_size += len(chunk)
                    else:
                        view = memoryview(artifact_source)
                        target.write(view)
                        artifact_digest.update(view)
                        artifact_size = view.nbytes
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(artifact_temporary, artifact_target)
                artifact_meta[name] = {
                    "size": artifact_size,
                    "sha256": artifact_digest.hexdigest(),
                }
            metadata = {
                "cache_schema": EXPORT_CACHE_SCHEMA_VERSION,
                "kind": kind, "key": key, "size": size,
                "sha256": digest.hexdigest(),
            }
            if plan is not None:
                metadata["plan"] = plan
            if artifact_meta:
                metadata["artifacts"] = artifact_meta
            with temporary_metadata.open("x", encoding="utf-8") as stream:
                json.dump(metadata, stream, sort_keys=True,
                          separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_payload, payload)
            os.replace(temporary_metadata, metadata_path)
            return CacheLookup(True, payload, metadata["sha256"], size,
                               "stored", plan)
        finally:
            temporary_payload.unlink(missing_ok=True)
            temporary_metadata.unlink(missing_ok=True)
            for temporary in temporary_artifacts:
                temporary.unlink(missing_ok=True)

    def lookup_artifacts(self, kind: str, key: str) -> dict[str, Path]:
        """Return verified auxiliary files, or an empty mapping on any fault."""
        payload, metadata_path = self._paths(kind, key)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (metadata.get("cache_schema") != EXPORT_CACHE_SCHEMA_VERSION
                    or metadata.get("kind") != kind
                    or metadata.get("key") != key):
                return {}
            result = {}
            for name, expected in metadata.get("artifacts", {}).items():
                if Path(name).name != name:
                    return {}
                path = payload.parent / name
                if (not path.is_file()
                        or path.stat().st_size != int(expected["size"])):
                    return {}
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(4 * 1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != expected["sha256"]:
                    return {}
                result[name] = path
            return result
        except (OSError, ValueError, KeyError, TypeError,
                json.JSONDecodeError):
            return {}
