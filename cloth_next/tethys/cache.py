# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned, versioned TETHYS wetness caches (no bpy).

A bake publishes ``cn_tethys_<id>.tethys`` (written atomically by the engine),
its preview image sequence in ``cn_tethys_<id>_frames/`` and the
authoritative sidecar ``cn_tethys_<id>.meta.json``. Only files whose sidecar
authenticates them are ever attached or deleted. Wetness caches are separate
from cloth playback: removing or rebaking one never touches a PC2.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re

from ..bake.cache_metadata import canonical_json, deterministic_hash, file_sha256, write_atomic
from ..core.safe_delete import delete_owned
from .protocol import CacheReader, ProtocolError

SCHEMA_VERSION = 1
KIND = "ClothNeXt.TETHYS"
PREFIX = "cn_tethys_"
_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class CacheState(str, Enum):
    READY = "READY"
    STALE = "STALE"
    PARTIAL = "PARTIAL"
    CORRUPT = "CORRUPT"
    MISSING = "MISSING"


@dataclass(frozen=True)
class CachePaths:
    root: Path
    stem: str

    @property
    def cache(self) -> Path:
        return self.root / f"{self.stem}.tethys"

    @property
    def sidecar(self) -> Path:
        return self.root / f"{self.stem}.meta.json"

    @property
    def frames(self) -> Path:
        return self.root / f"{self.stem}_frames"

    def frame_image(self, frame: int) -> Path:
        return self.frames / f"{self.stem}_{frame:04d}.exr"

    def normal_image(self, frame: int) -> Path:
        # Tangent-space water normal (UV layout only).
        return self.frames / f"{self.stem}_normal_{frame:04d}.exr"

    @property
    def request(self) -> Path:
        return self.root / f"{self.stem}.request.tethysrq"

    @property
    def environment(self) -> Path:
        return self.root / f"{self.stem}.water.tethysev"

    @property
    def cancel(self) -> Path:
        return self.root / f"{self.stem}.cancel"


def paths_for(root: Path, object_uuid: str) -> CachePaths:
    token = _SAFE.sub("", str(object_uuid))[:12] or "object"
    return CachePaths(Path(root), f"{PREFIX}{token}")


def fingerprints(**parts) -> dict:
    values = {key: deterministic_hash(value) if isinstance(value, dict) else str(value)
              for key, value in sorted(parts.items())}
    values["combined"] = deterministic_hash(values)
    return values


def _digest(record: dict) -> str:
    return deterministic_hash({k: v for k, v in record.items() if k != "metadata_digest"})


def write_partial(paths: CachePaths, record: dict) -> dict:
    payload = {"schema_version": SCHEMA_VERSION, "kind": KIND, "completion_state": "partial",
               "cache_file": paths.cache.name, **record}
    payload["metadata_digest"] = _digest(payload)
    write_atomic(paths.sidecar, payload)
    return payload


def write_complete(paths: CachePaths, partial: dict, *, images: dict, result: dict) -> dict:
    """Authenticate the engine's published cache and seal the sidecar."""
    with CacheReader(paths.cache, verify_all=True) as reader:
        layout = {"polygons": reader.polygon_count, "frames": [reader.first_frame, reader.last_frame],
                  "fps": reader.fps, "channels": reader.channels}
        identity = reader.identity
    if identity != partial.get("identity_json"):
        raise ProtocolError("The wetness cache belongs to a different bake")
    payload = {k: v for k, v in partial.items() if k != "metadata_digest"}
    payload.update(completion_state="complete", cache_size=paths.cache.stat().st_size,
                   cache_sha256=file_sha256(paths.cache), layout=layout, images=images, result=result)
    payload["metadata_digest"] = _digest(payload)
    write_atomic(paths.sidecar, payload)
    return payload


def mark_failed(paths: CachePaths, state: str, message: str) -> None:
    try:
        current = json.loads(paths.sidecar.read_text("utf-8"))
    except (OSError, ValueError):
        current = {"schema_version": SCHEMA_VERSION, "kind": KIND, "cache_file": paths.cache.name}
    if current.get("completion_state") == "complete":
        return
    current = {k: v for k, v in current.items() if k != "metadata_digest"}
    current.update(completion_state=state, message=message[:500])
    current["metadata_digest"] = _digest(current)
    write_atomic(paths.sidecar, current)


def load_sidecar(paths: CachePaths) -> dict | None:
    try:
        payload = json.loads(paths.sidecar.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"completion_state": "corrupt"}
    if not isinstance(payload, dict) or payload.get("kind") != KIND or \
            payload.get("schema_version") != SCHEMA_VERSION or payload.get("metadata_digest") != _digest(payload):
        return {"completion_state": "corrupt"}
    return payload


def inspect(paths: CachePaths, expected_fingerprint: str | None = None, *, full: bool = False):
    """Classify the owned cache. `full` re-hashes the cache file."""
    record = load_sidecar(paths)
    if record is None:
        return CacheState.MISSING, None
    state = record.get("completion_state")
    if state == "corrupt":
        return CacheState.CORRUPT, None
    if state != "complete":
        return CacheState.PARTIAL, record
    try:
        if paths.cache.stat().st_size != record["cache_size"]:
            return CacheState.CORRUPT, record
        if full and file_sha256(paths.cache) != record["cache_sha256"]:
            return CacheState.CORRUPT, record
        with CacheReader(paths.cache, verify_all=full) as reader:
            if reader.identity != record.get("identity_json"):
                return CacheState.CORRUPT, record
        images = record.get("images") or {}
        for frame in range(record["layout"]["frames"][0], record["layout"]["frames"][1] + 1):
            if images and not paths.frame_image(frame).is_file():
                return CacheState.CORRUPT, record
    except FileNotFoundError:
        return CacheState.MISSING, record
    except (OSError, KeyError, TypeError, ProtocolError):
        return CacheState.CORRUPT, record
    if expected_fingerprint is not None and record["fingerprints"]["combined"] != expected_fingerprint:
        return CacheState.STALE, record
    return CacheState.READY, record


def remove(paths: CachePaths, *, stage: str = "TETHYS_CLEAR", keep_sidecar: bool = False) -> list:
    """Delete only this object's owned TETHYS files inside the cache folder."""
    results = []
    targets = [paths.cache, paths.cache.with_name(paths.cache.name + ".partial"),
               paths.request, paths.environment, paths.cancel]
    if not keep_sidecar:
        targets.append(paths.sidecar)
    for target in targets:
        if target.exists():
            results.append(delete_owned(target, root=paths.root, ownership_authenticated=True,
                                        lifecycle_stage=stage, artifact_type="tethys_cache"))
    if paths.frames.is_dir():
        results.append(delete_owned(paths.frames, root=paths.root, ownership_authenticated=True,
                                    lifecycle_stage=stage, artifact_type="tethys_frames", recursive=True))
    return results


def remove_scratch(paths: CachePaths) -> None:
    """Request, water stream and cancel marker are only needed during a bake."""
    for target in (paths.request, paths.environment, paths.cancel,
                   paths.cache.with_name(paths.cache.name + ".partial")):
        if target.exists():
            delete_owned(target, root=paths.root, ownership_authenticated=True,
                         lifecycle_stage="TETHYS_SCRATCH", artifact_type="tethys_scratch")


__all__ = ["CachePaths", "CacheState", "canonical_json", "fingerprints", "inspect", "load_sidecar",
           "mark_failed", "paths_for", "remove", "remove_scratch", "write_complete", "write_partial"]
