# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned, deterministic metadata for published playback caches."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import uuid
from typing import Any, Mapping

from . import pc2

CACHE_METADATA_SCHEMA_VERSION = 1
HASH_ALGORITHM = "sha256"


class CacheMetadataError(ValueError):
    pass


class CacheCondition(str, Enum):
    READY = "READY"
    MISSING = "MISSING"
    PARTIAL = "PARTIAL"
    CORRUPT = "CORRUPT"
    STALE_SETTINGS = "STALE_SETTINGS"
    STALE_GEOMETRY = "STALE_GEOMETRY"


@dataclass(frozen=True, slots=True)
class CacheInspection:
    condition: CacheCondition
    message: str
    metadata: Mapping[str, Any] | None = None

    @property
    def usable(self) -> bool:
        return self.condition is CacheCondition.READY


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True)


def deterministic_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sidecar_path(cache_path: Path) -> Path:
    return Path(cache_path).with_suffix(".meta.json")


def write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably replace one JSON document without exposing a partial write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2,
                                    sort_keys=True))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def partial_metadata(*, cache_path: Path, fingerprints: Mapping[str, str],
                     identities: Mapping[str, Any], expected: Mapping[str, Any],
                     details: Mapping[str, Any]) -> dict[str, Any]:
    """Create the fail-closed record installed before cache generation."""
    return {
        "schema_version": CACHE_METADATA_SCHEMA_VERSION,
        "completion_state": "partial",
        "cache_format": "POINTCACHE2",
        "cache_file": Path(cache_path).name,
        "hash_algorithm": HASH_ALGORITHM,
        "fingerprints": dict(fingerprints),
        "identities": dict(identities),
        "expected": dict(expected),
        "details": dict(details),
    }


def completed_metadata(partial: Mapping[str, Any], *, cache_path: Path,
                       timings: Mapping[str, float] | None = None) \
        -> dict[str, Any]:
    """Validate the PC2 and return the immutable complete sidecar payload."""
    cache_path = Path(cache_path)
    header = pc2.read_header(cache_path)
    expected = partial.get("expected")
    if not isinstance(expected, Mapping):
        raise CacheMetadataError("cache metadata has no expected layout")
    checks = {
        "vertex_count": header.vertex_count,
        "frame_count": header.frame_count,
        "start_frame": header.start_frame,
        "sample_rate": header.sample_rate,
    }
    for name, actual in checks.items():
        if expected.get(name) != actual:
            raise CacheMetadataError(
                f"published PC2 {name} is {actual!r}, expected "
                f"{expected.get(name)!r}")
    result = dict(partial)
    result.update({
        "completion_state": "complete",
        "cache_file": cache_path.name,
        "cache_size": cache_path.stat().st_size,
        "cache_sha256": file_sha256(cache_path),
        "pc2": {
            "format_version": pc2.PC2_VERSION,
            "writer_version": pc2.PC2_WRITER_VERSION,
            **checks,
        },
    })
    if timings is not None:
        result["timings"] = dict(timings)
    # The digest covers every semantic field except itself.
    result["metadata_digest"] = deterministic_hash(result)
    return result


def _load(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CacheMetadataError(f"metadata cannot be read: {exc}") from exc
    if not isinstance(value, dict):
        raise CacheMetadataError("metadata root is not an object")
    return value


def _mapping(metadata: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = metadata.get(name)
    if not isinstance(value, Mapping):
        raise CacheMetadataError(f"{name} is missing or is not an object")
    return value


def _require_nonempty_strings(value: Mapping[str, Any], names: set[str],
                              label: str) -> None:
    missing = sorted(name for name in names
                     if not isinstance(value.get(name), str)
                     or not value.get(name))
    if missing:
        raise CacheMetadataError(
            f"{label} has missing/invalid fields: {', '.join(missing)}")


def inspect_cache(cache_path: Path, *,
                  settings_fingerprint: str | None = None,
                  geometry_fingerprint: str | None = None) -> CacheInspection:
    """Fully authenticate one cache/sidecar pair and classify invalidation."""
    cache_path = Path(cache_path)
    metadata_path = sidecar_path(cache_path)
    if not metadata_path.is_file():
        return CacheInspection(CacheCondition.MISSING,
                               "Cache metadata is missing")
    try:
        metadata = _load(metadata_path)
        if metadata.get("schema_version") != CACHE_METADATA_SCHEMA_VERSION:
            raise CacheMetadataError("unsupported cache metadata schema")
        if metadata.get("completion_state") != "complete":
            return CacheInspection(CacheCondition.PARTIAL,
                                   "Cache generation did not complete",
                                   metadata)
        if metadata.get("cache_format") != "POINTCACHE2":
            raise CacheMetadataError("unexpected cache format")
        if metadata.get("cache_file") != cache_path.name:
            raise CacheMetadataError("metadata names a different cache file")
        if metadata.get("hash_algorithm") != HASH_ALGORITHM:
            raise CacheMetadataError("unsupported cache hash algorithm")
        if not cache_path.is_file():
            raise CacheMetadataError("published PC2 file is missing")
        if metadata.get("cache_size") != cache_path.stat().st_size:
            raise CacheMetadataError("published PC2 size changed")
        digest = metadata.get("metadata_digest")
        unsigned = dict(metadata)
        unsigned.pop("metadata_digest", None)
        if not isinstance(digest, str) or digest != deterministic_hash(unsigned):
            raise CacheMetadataError("metadata digest mismatch")
        cache_digest = metadata.get("cache_sha256")
        if (not isinstance(cache_digest, str)
                or cache_digest != file_sha256(cache_path)):
            raise CacheMetadataError("published PC2 hash mismatch")
        header = pc2.read_header(cache_path)
        declared = metadata.get("pc2")
        if not isinstance(declared, Mapping):
            raise CacheMetadataError("PC2 layout metadata is missing")
        writer_version = declared.get("writer_version")
        if not isinstance(writer_version, int) or writer_version < 1:
            raise CacheMetadataError("invalid PC2 writer version")
        actual = {
            "format_version": pc2.PC2_VERSION,
            # Writer identity is provenance, not an invalidation key. A newer
            # add-on must keep authentic older PC2 files usable.
            "writer_version": writer_version,
            "vertex_count": header.vertex_count,
            "frame_count": header.frame_count,
            "start_frame": header.start_frame,
            "sample_rate": header.sample_rate,
        }
        if dict(declared) != actual:
            raise CacheMetadataError("PC2 layout metadata mismatch")
        fingerprints = _mapping(metadata, "fingerprints")
        _require_nonempty_strings(
            fingerprints,
            {"settings", "geometry", "combined", "topology", "object", "scene"},
            "fingerprints")
        identities = _mapping(metadata, "identities")
        _require_nonempty_strings(
            identities, {"cloth_next_version", "blender_version"},
            "identities")
        _mapping(identities, "object")
        _mapping(identities, "solver")
        _mapping(metadata, "details")
        expected = _mapping(metadata, "expected")
        expected_layout = {
            "vertex_count": header.vertex_count,
            "frame_count": header.frame_count,
            "start_frame": header.start_frame,
            "sample_rate": header.sample_rate,
        }
        if dict(expected) != expected_layout:
            raise CacheMetadataError("expected PC2 layout mismatch")
        if (settings_fingerprint is not None
                and fingerprints.get("settings") != settings_fingerprint):
            return CacheInspection(CacheCondition.STALE_SETTINGS,
                                   "Cache settings changed", metadata)
        if (geometry_fingerprint is not None
                and fingerprints.get("geometry") != geometry_fingerprint):
            return CacheInspection(CacheCondition.STALE_GEOMETRY,
                                   "Cache geometry changed", metadata)
        return CacheInspection(CacheCondition.READY, "Cache ready", metadata)
    except (CacheMetadataError, pc2.Pc2Error, OSError) as exc:
        return CacheInspection(CacheCondition.CORRUPT,
                               f"Cache is damaged: {exc}")
