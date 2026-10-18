# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The two user-facing solver engines and how installations map onto them.

AETHER is the official, unmodified ST-Tech PPF Contact Solver. GAIA is the
separately maintained PolyWorks build. An installation is GAIA only when its
root carries a valid ``gaia-build.json``; everything else is AETHER. Official
managed releases are verified upstream builds; an external installation
without GAIA identity is shown as an unverified AETHER build because Cloth
NeXt cannot prove it is unmodified.

Pure local file inspection: no network, no process start.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath

from .solver_registry import SolverInstallation, SolverRegistry

AETHER = "AETHER"
GAIA = "GAIA"
ENGINES = (AETHER, GAIA)
GAIA_BUILD_FILE = "gaia-build.json"
GAIA_BUILD_KIND = "ClothNeXt.GaiaBuild.v1"
GAIA_SOURCE = "gaia"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{7,40}")
_BUILD_ID = re.compile(r"gaia-[0-9A-Za-z.\-]+\+[0-9A-Za-z.\-]+\.(windows-x64|linux-x64)\.[0-9a-f]{12}")
_MAX_BUILD_FILE = 256 * 1024

# GAIA's fast deterministic path (docs/GAIA_FAST_DETERMINISTIC_SOLVER_20261018.md):
# the reference ordering switches plus the exact parallel replays. Opt-in only.
REPRODUCIBLE_ENVIRONMENT = (
    ("PPF_REPRO_FAST_CONTACT", "1"),
    ("PPF_REPRO_FAST_SERIAL", "1"),
    ("PPF_REPRO_FILTERED_CONTACT", "1"),
    ("PPF_REPRO_ORDER_FILTER", "all"),
    ("PPF_REPRO_ORDER_MODE", "colored"),
    ("PPF_REPRO_SERIAL_CSR", "1"),
    ("PPF_REPRO_SERIAL_DIRICHLET", "1"),
    ("PPF_REPRO_SERIAL_STITCH", "1"),
    ("PPF_REPRO_WALK_SPMV", "1"),
)

ENGINE_DESCRIPTIONS = {
    AETHER: "Official, unmodified PPF Contact Solver by ST-Tech",
    GAIA: "PolyWorks' separately maintained patched PPF solver",
}


@dataclass(frozen=True, slots=True)
class GaiaBuild:
    version: str
    build_id: str
    source_revision: str
    platform: str
    backend: str
    base_release_tag: str
    protocol_version: str
    files: tuple[tuple[str, str], ...]  # (relative posix path, sha256)

    @property
    def short_revision(self) -> str:
        return self.source_revision[:12]


def parse_gaia_build(payload: object) -> GaiaBuild:
    """Validate a ``gaia-build.json`` payload; raise ValueError when invalid."""
    if not isinstance(payload, dict) or payload.get("kind") != GAIA_BUILD_KIND:
        raise ValueError("not a GAIA build description")
    fields = {}
    for name in ("version", "build_id", "source_revision", "platform", "backend",
                 "base_release_tag", "protocol_version"):
        value = payload.get(name)
        if not isinstance(value, str) or not value or len(value) > 128:
            raise ValueError(f"GAIA build field {name!r} is missing or invalid")
        fields[name] = value
    if not _REVISION.fullmatch(fields["source_revision"]):
        raise ValueError("GAIA source revision must be a lowercase git hash")
    if not _BUILD_ID.fullmatch(fields["build_id"]):
        raise ValueError("GAIA build id has an unexpected format")
    if fields["platform"] not in ("windows-x64", "linux-x64"):
        raise ValueError("GAIA build platform is not supported")
    raw_files = payload.get("files")
    if not isinstance(raw_files, dict) or not raw_files:
        raise ValueError("GAIA build must list its patched files")
    files = []
    for name, digest in sorted(raw_files.items()):
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ValueError("GAIA build file entries must be strings")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError(f"GAIA build file path is unsafe: {name!r}")
        if not _HEX64.fullmatch(digest):
            raise ValueError(f"GAIA build checksum for {name!r} is invalid")
        files.append((name, digest))
    return GaiaBuild(files=tuple(files), **fields)


def _file_signature(path: Path):
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


@lru_cache(maxsize=32)
def _read_gaia_build_cached(path: Path, _signature) -> GaiaBuild | None:
    try:
        if path.stat().st_size > _MAX_BUILD_FILE:
            return None
        return parse_gaia_build(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError):
        return None


def read_gaia_build(root: Path) -> GaiaBuild | None:
    """Return the GAIA identity of an installation root, or None."""
    path = Path(root) / GAIA_BUILD_FILE
    signature = _file_signature(path)
    if signature is None:
        return None
    return _read_gaia_build_cached(path, signature)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_gaia_files(root: Path, build: GaiaBuild) -> str | None:
    """Return None when every patched file matches, else a user-facing problem."""
    root = Path(root)
    for name, expected in build.files:
        path = root.joinpath(*PurePosixPath(name).parts)
        if not path.is_file():
            return f"GAIA file {name} is missing. Repair GAIA in Preferences."
        if sha256_file(path) != expected:
            return f"GAIA file {name} does not match its build checksum. Repair GAIA in Preferences."
    return None


def _gaia_root(installation: SolverInstallation) -> Path | None:
    # GAIA packages are registered with their package root; an external
    # registration may point at target/<backend>/release below it.
    raw = getattr(installation, "root_path", None)
    if not raw:
        return None
    root = Path(raw)
    if root.name == "release" and root.parent.parent.name == "target":
        return root.parent.parent.parent
    return root


def gaia_build_of(installation: SolverInstallation | None) -> GaiaBuild | None:
    root = _gaia_root(installation) if installation is not None else None
    return read_gaia_build(root) if root is not None else None


def engine_of(installation: SolverInstallation) -> str:
    return GAIA if gaia_build_of(installation) is not None else AETHER


def verified_upstream(installation: SolverInstallation) -> bool:
    """A managed official release downloaded and verified by Cloth NeXt."""
    return bool(getattr(installation, "managed", False)
                and getattr(installation, "source", "") == "official"
                and getattr(installation, "official_release_tag", None))


def installation_label(installation: SolverInstallation) -> str:
    """Engine-centred name; the historic release codename is never shown."""
    build = gaia_build_of(installation)
    if build is not None:
        return f"GAIA {build.version} · build {build.short_revision}"
    if verified_upstream(installation):
        return f"AETHER · upstream {installation.official_release_tag}"
    return ("AETHER · external, unverified build (protocol "
            f"{getattr(installation, 'protocol_version', None) or '?'})")


def installations_for(registry: SolverRegistry, engine: str) -> tuple[SolverInstallation, ...]:
    return tuple(item for item in registry.installations if engine_of(item) == engine)


def _selectable(item: SolverInstallation) -> bool:
    return bool(item.available and item.compatible and item.verified and item.healthy)


def best_installation(registry: SolverRegistry, engine: str) -> SolverInstallation | None:
    """Newest healthy installation of one engine; verified upstream before external."""
    candidates = [item for item in installations_for(registry, engine) if _selectable(item)]
    if not candidates:
        return None

    def rank(item: SolverInstallation):
        protocol = tuple(int(part) for part in re.findall(r"\d+", item.protocol_version or ""))
        build = gaia_build_of(item)
        return (verified_upstream(item) or build is not None, protocol,
                item.official_release_tag or (build.version if build else ""))
    return max(candidates, key=rank)


def selected_engine(registry: SolverRegistry) -> str | None:
    selected = registry.selected
    return engine_of(selected) if selected is not None else None
