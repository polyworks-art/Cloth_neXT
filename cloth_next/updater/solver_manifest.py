# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Strict, metadata-only validation of the solver compatibility manifest.

The manifest pins the exact official ``st-tech/ppf-contact-solver`` release that
Cloth NeXt has verified. It never carries binary data, mirrors, forks, CI
artifacts, local paths, or mutable ``latest`` references.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

SUPPORTED_MANIFEST_VERSIONS = frozenset({1, 2})
OFFICIAL_OWNER = "st-tech"
OFFICIAL_REPOSITORY = "ppf-contact-solver"
OFFICIAL_REPOSITORY_SLUG = f"{OFFICIAL_OWNER}/{OFFICIAL_REPOSITORY}"
OFFICIAL_DOWNLOAD_PREFIX = (
    f"https://github.com/{OFFICIAL_REPOSITORY_SLUG}/releases/download/")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER_RE = re.compile(r"VERIFIED|PLACEHOLDER|TODO|CHANGEME|EXAMPLE", re.IGNORECASE)
_LOCAL_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\|^/|^\.")


@dataclass(frozen=True, slots=True)
class SolverCompatibilityEntry:
    platform: str
    solver_package_version: str
    protocol_version: str
    schema_version: str
    official_repository: str
    official_release_tag: str
    official_asset_name: str
    official_asset_url: str
    download_size: int
    sha256: str
    archive_layout_version: int
    health_check_required: bool
    release_id: str = ""
    display_name: str = ""
    channel: str = "stable"

    @property
    def official_release_page(self) -> str:
        return (f"https://github.com/{OFFICIAL_REPOSITORY_SLUG}"
                f"/releases/tag/{self.official_release_tag}")


@dataclass(frozen=True, slots=True)
class SolverCompatibilityManifest:
    manifest_version: int
    cloth_next_version: str
    platforms: tuple[SolverCompatibilityEntry, ...]
    default_releases: tuple[tuple[str, str], ...] = ()

    def entry_for(self, platform: str) -> SolverCompatibilityEntry | None:
        default = dict(self.default_releases).get(platform)
        entries = self.releases_for(platform)
        return next((entry for entry in entries
                     if entry.release_id == default), entries[0] if entries else None)

    def releases_for(self, platform: str) -> tuple[SolverCompatibilityEntry, ...]:
        return tuple(entry for entry in self.platforms
                     if entry.platform == platform)

    def release(self, platform: str,
                release_id: str) -> SolverCompatibilityEntry | None:
        return next((entry for entry in self.platforms
                     if entry.platform == platform
                     and entry.release_id == release_id), None)


def _require_text(mapping: Mapping[str, Any], key: str, platform: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{platform}: {key} must be a non-empty string")
    if _PLACEHOLDER_RE.search(value):
        raise ValueError(f"{platform}: {key} contains an unverified placeholder value")
    return value.strip()


def _validate_url(url: str, tag: str, asset: str, platform: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"{platform}: official_asset_url must use https")
    if parts.netloc != "github.com":
        raise ValueError(f"{platform}: official_asset_url must point at github.com")
    if _LOCAL_PATH_RE.match(url) or "\\" in url:
        raise ValueError(f"{platform}: official_asset_url must not be a local path")
    expected = f"{OFFICIAL_DOWNLOAD_PREFIX}{tag}/{asset}"
    if url != expected:
        raise ValueError(
            f"{platform}: official_asset_url must be the immutable official release "
            f"asset URL {expected!r}, got {url!r}")
    if "latest" in (tag.lower(), *(part.lower() for part in parts.path.split("/"))):
        raise ValueError(f"{platform}: mutable 'latest' references are forbidden")


def parse_entry(platform: str, payload: Mapping[str, Any], *,
                legacy: bool = False) -> SolverCompatibilityEntry:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{platform}: platform entry must be an object")
    repository = _require_text(payload, "official_repository", platform)
    if repository != OFFICIAL_REPOSITORY_SLUG:
        raise ValueError(
            f"{platform}: only the official repository {OFFICIAL_REPOSITORY_SLUG!r} "
            f"is allowed, got {repository!r}")
    tag = _require_text(payload, "official_release_tag", platform)
    if tag.lower() == "latest":
        raise ValueError(f"{platform}: a blind 'latest' release tag is forbidden")
    asset = _require_text(payload, "official_asset_name", platform)
    url = _require_text(payload, "official_asset_url", platform)
    _validate_url(url, tag, asset, platform)
    sha256 = _require_text(payload, "sha256", platform).lower()
    if not _SHA256_RE.match(sha256):
        raise ValueError(f"{platform}: sha256 must be 64 lowercase hex characters")
    size = payload.get("download_size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError(f"{platform}: download_size must be a positive integer")
    layout = payload.get("archive_layout_version")
    if layout != 1:
        raise ValueError(f"{platform}: unsupported archive_layout_version {layout!r}")
    health = payload.get("health_check_required")
    if health is not True:
        raise ValueError(f"{platform}: health_check_required must be true")
    return SolverCompatibilityEntry(
        platform=platform,
        release_id=(
            f"legacy-{tag}" if legacy
            else _require_text(payload, "id", platform)),
        display_name=(
            f"PPF Contact Solver {tag}" if legacy
            else _require_text(payload, "display_name", platform)),
        channel=(
            "stable" if legacy
            else _require_text(payload, "channel", platform).lower()),
        solver_package_version=_require_text(payload, "solver_package_version", platform),
        protocol_version=_require_text(payload, "protocol_version", platform),
        schema_version=_require_text(payload, "schema_version", platform),
        official_repository=repository,
        official_release_tag=tag,
        official_asset_name=asset,
        official_asset_url=url,
        download_size=size,
        sha256=sha256,
        archive_layout_version=layout,
        health_check_required=health,
    )


def parse_manifest(payload: Mapping[str, Any], *,
                   expected_cloth_next_version: str | None = None,
                   ) -> SolverCompatibilityManifest:
    if not isinstance(payload, Mapping):
        raise ValueError("solver compatibility manifest must be a JSON object")
    version = payload.get("manifest_version")
    if version not in SUPPORTED_MANIFEST_VERSIONS:
        raise ValueError(f"unsupported manifest_version {version!r}")
    cloth_next_version = payload.get("cloth_next_version")
    if not isinstance(cloth_next_version, str) or not cloth_next_version:
        raise ValueError("cloth_next_version must be a non-empty string")
    if (expected_cloth_next_version is not None
            and cloth_next_version != expected_cloth_next_version):
        raise ValueError(
            f"cloth_next_version {cloth_next_version!r} does not match the "
            f"blender_manifest.toml version {expected_cloth_next_version!r}")
    platforms = payload.get("platforms")
    if not isinstance(platforms, Mapping) or not platforms:
        raise ValueError("platforms must be a non-empty object")
    entries: list[SolverCompatibilityEntry] = []
    defaults: list[tuple[str, str]] = []
    if version == 1:
        entries = [parse_entry(name, entry, legacy=True)
                   for name, entry in sorted(platforms.items())]
        defaults = [(entry.platform, entry.release_id) for entry in entries]
    else:
        for platform, platform_payload in sorted(platforms.items()):
            if not isinstance(platform_payload, Mapping):
                raise ValueError(f"{platform}: platform entry must be an object")
            default = _require_text(
                platform_payload, "default_release_id", platform)
            releases = platform_payload.get("releases")
            if not isinstance(releases, list) or not releases:
                raise ValueError(f"{platform}: releases must be a non-empty list")
            parsed = tuple(parse_entry(platform, item) for item in releases)
            ids = tuple(item.release_id for item in parsed)
            if len(ids) != len(set(ids)):
                raise ValueError(f"{platform}: release ids must be unique")
            if default not in ids:
                raise ValueError(
                    f"{platform}: default_release_id {default!r} is not listed")
            entries.extend(parsed)
            defaults.append((platform, default))
    return SolverCompatibilityManifest(
        version, cloth_next_version, tuple(entries), tuple(defaults))


def bundled_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "solver_compatibility.json"


def load_bundled_manifest(*, expected_cloth_next_version: str | None = None,
                          ) -> SolverCompatibilityManifest:
    payload = json.loads(bundled_manifest_path().read_text(encoding="utf-8"))
    return parse_manifest(payload, expected_cloth_next_version=expected_cloth_next_version)


def download_availability(payload: Mapping[str, Any], platform: str,
                          ) -> tuple[SolverCompatibilityEntry | None, str | None]:
    """Return (entry, None) when a verified download exists, else (None, reason).

    When no verified official URL or checksum is available yet, the automatic
    download must stay disabled and the UI offers only "Select Existing
    Installation" and "Open Official Download Page".
    """
    try:
        manifest = parse_manifest(payload)
    except ValueError as exc:
        return None, str(exc)
    entry = manifest.entry_for(platform)
    if entry is None:
        return None, f"no verified solver release is listed for platform {platform!r}"
    return entry, None
