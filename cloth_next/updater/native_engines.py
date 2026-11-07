# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native engines (AETHER, TETHYS) installed from the PolyWorks server.

The engines are no longer part of the add-on ZIP. A package is a ZIP with
``engine.json`` (identity, size, SHA-256, protocol) and ``bin/<filename>``.
Installation is transactional: archive size/SHA-256 from the server → safe
extraction into staging → identity and binary checks → ``--version`` probe →
atomic move to ``engines/<engine>/<install id>`` → ``current.json``. Any
failure leaves the previous engine installed and in use. One older build is
kept for rollback; builds that are still running (Windows refuses to delete
them) are removed on a later install.

The installed engine lives outside the extension folder, so Blender can
replace the add-on while a Live Simulation or a bake keeps an engine running.

No ``bpy``; runs on a worker thread.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile
from typing import Callable

from ..platform_support import managed_solver_root, platform_spec

PACKAGE_KIND = "ClothNeXt.NativeEngine.v1"
PACKAGE_FILE = "engine.json"
CURRENT_FILE = "current.json"
ROOT_ENV = "CLOTH_NEXT_ENGINES_ROOT"   # tests and isolated validation only
AETHER = "aether"
TETHYS = "tethys"
_INSTALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MAX_PACKAGE_FILE = 64 * 1024


class EngineInstallError(RuntimeError):
    """User-facing installation failure; nothing was changed."""


@dataclass(frozen=True, slots=True)
class EngineSpec:
    engine: str
    label: str
    filenames: dict
    backend: str
    gaia_gated: bool

    def filename(self, platform: str) -> str | None:
        return self.filenames.get(platform)


SPECS = {
    AETHER: EngineSpec(AETHER, "AETHER Engine", {"windows-x64": "AETHER Engine.exe"}, "cuda", False),
    TETHYS: EngineSpec(TETHYS, "TETHYS Engine",
                       {"windows-x64": "TETHYS Engine.exe", "linux-x64": "tethys-engine"}, "cpu", True),
}


def spec(engine: str) -> EngineSpec:
    try:
        return SPECS[engine]
    except KeyError:
        raise ValueError(f"unknown native engine {engine!r}") from None


def engines_root(environ=None) -> Path:
    env = os.environ if environ is None else environ
    override = (env.get(ROOT_ENV) or "").strip()
    if override:
        return Path(override).resolve()
    return managed_solver_root(environ=environ).parent / "engines"


def current_platform() -> str:
    return platform_spec().blender_platform


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class InstalledEngine:
    engine: str
    install_id: str
    root: Path
    manifest: dict            # engine.json of the package
    server_build_id: str      # the server's build id ("" when installed from a local file)

    @property
    def binary(self) -> Path:
        return self.root / "bin" / self.manifest["filename"]

    @property
    def version(self) -> str:
        return str(self.manifest.get("version") or "?")


# ------------------------------------------------------------ server builds
def choose_build(builds: list, engine: str, addon_version: str, *, platform: str | None = None) -> dict | None:
    """Newest listed build of ``engine`` for this computer and add-on version."""
    from ..polyworks.gaia_downloads import _version_key
    item = spec(engine)
    selected = platform or current_platform()
    local = _version_key(addon_version)
    usable = []
    for build in builds:
        if build.get("engine") != engine or build.get("platform") != selected:
            continue
        if build.get("backend") not in (None, item.backend):
            continue
        minimum, maximum = build.get("min_addon_version"), build.get("max_addon_version")
        if (minimum and local < _version_key(minimum)) or (maximum and local > _version_key(maximum)):
            continue
        usable.append(build)
    return max(usable, key=lambda b: (b.get("published_at") or "", b["version"])) if usable else None


# ------------------------------------------------------------ package identity
def check_manifest(payload, engine: str, platform: str, compatible: Callable[[dict], str | None]) -> dict:
    """The package's engine.json, validated for this engine, platform and add-on."""
    item = spec(engine)
    if not isinstance(payload, dict) or payload.get("kind") != PACKAGE_KIND or payload.get("schema_version") != 1:
        raise EngineInstallError(f"The download is not a {item.label} package.")
    if payload.get("engine") != engine:
        raise EngineInstallError(f"The download is not a {item.label} package.")
    filename = item.filename(platform)
    if filename is None:
        raise EngineInstallError(f"The {item.label} is not available for {platform}.")
    if payload.get("platform") != platform or payload.get("filename") != filename:
        raise EngineInstallError(f"The {item.label} package is for another computer type.")
    size, digest = payload.get("file_size"), payload.get("sha256")
    if not isinstance(size, int) or size <= 0 or not isinstance(digest, str) or not _HEX64.match(digest):
        raise EngineInstallError(f"The {item.label} package description is invalid.")
    for name in ("version", "build_id"):
        if not isinstance(payload.get(name), str) or not payload[name]:
            raise EngineInstallError(f"The {item.label} package description is invalid.")
    problem = compatible(payload)
    if problem:
        raise EngineInstallError(problem)
    return payload


def _read_manifest(root: Path) -> dict | None:
    path = root / PACKAGE_FILE
    try:
        if path.stat().st_size > _MAX_PACKAGE_FILE:
            return None
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def verify_binary(root: Path, manifest: dict) -> str | None:
    """None when ``bin/<filename>`` matches size and SHA-256, else the reason."""
    binary = root / "bin" / manifest["filename"]
    try:
        if binary.stat().st_size != manifest["file_size"]:
            return "damaged"
        return None if sha256_file(binary) == manifest["sha256"] else "damaged"
    except OSError:
        return "missing"


# ------------------------------------------------------------ installed state
def _engine_dir(engine: str, root: Path | None) -> Path:
    return (root or engines_root()) / spec(engine).engine


def installed(engine: str, *, root: Path | None = None, platform: str | None = None) -> InstalledEngine | None:
    """The installed engine named by current.json (cheap: no hashing)."""
    base = _engine_dir(engine, root)
    try:
        pointer = json.loads((base / CURRENT_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    install_id = pointer.get("install_id") if isinstance(pointer, dict) else None
    if not isinstance(install_id, str) or not _INSTALL_ID.match(install_id):
        return None
    folder = base / install_id
    manifest = _read_manifest(folder)
    selected = platform or current_platform()
    if (not isinstance(manifest, dict) or manifest.get("engine") != engine
            or manifest.get("platform") != selected or manifest.get("filename") != spec(engine).filename(selected)):
        return None
    build = pointer.get("server_build_id")
    return InstalledEngine(engine, install_id, folder, manifest, build if isinstance(build, str) else "")


def install_id_for(manifest: dict) -> str:
    raw = f"{manifest['version']}-{manifest['build_id']}-{manifest['sha256'][:12]}"
    value = re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:120]
    if not _INSTALL_ID.match(value):
        raise EngineInstallError("The engine package has an unusable version.")
    return value


def _write_current(base: Path, install_id: str, server_build_id: str) -> None:
    payload = {"schema_version": 1, "install_id": install_id, "server_build_id": server_build_id}
    temporary = base / f".{CURRENT_FILE}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, base / CURRENT_FILE)


def _prune(base: Path, keep: set[str]) -> None:
    """Remove other installed builds; a running engine stays until the next install."""
    for child in base.iterdir():
        if child.is_dir() and child.name not in keep:
            shutil.rmtree(child, ignore_errors=True)


def _package_root(staging: Path) -> Path:
    if (staging / PACKAGE_FILE).is_file():
        return staging
    children = list(staging.iterdir())
    if len(children) == 1 and children[0].is_dir() and (children[0] / PACKAGE_FILE).is_file():
        return children[0]
    raise EngineInstallError("The download is not an engine package (engine.json is missing).")


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0


def run_version(binary: Path, timeout: float = 15.0) -> str:
    """``--version`` output of an engine binary (no GPU work)."""
    try:
        result = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                                timeout=timeout, creationflags=_creation_flags())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineInstallError(f"The engine could not start on this computer ({exc.__class__.__name__}).") from exc
    if result.returncode != 0:
        raise EngineInstallError("The engine could not start on this computer.")
    return result.stdout.strip()


def _move_into_place(source: Path, target: Path, attempts: int = 8) -> None:
    """Rename the verified staging folder. A virus scanner briefly holds a freshly
    written or just started executable open, so Windows may refuse for a moment."""
    import time
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


@dataclass(frozen=True, slots=True)
class EngineInstallResult:
    installed: InstalledEngine
    already_installed: bool = False


def install_package(engine: str, archive: Path, *,
                    compatible: Callable[[dict], str | None],
                    probe: Callable[[Path, dict], str | None],
                    expected_sha256: str | None = None,
                    expected_size: int | None = None,
                    server_build_id: str = "",
                    root: Path | None = None,
                    platform: str | None = None,
                    cancelled: Callable[[], bool] = lambda: False) -> EngineInstallResult:
    """Install ``archive`` as the current ``engine``. ``probe(binary, manifest)``
    returns None when the binary identifies as the package says, else the reason."""
    from .archive import extract_to_staging
    item = spec(engine)
    selected = platform or current_platform()
    archive = Path(archive)
    if expected_size is not None and archive.stat().st_size != expected_size:
        raise EngineInstallError(f"The {item.label} download has the wrong size; it was not installed.")
    if expected_sha256 is not None and sha256_file(archive) != expected_sha256.lower():
        raise EngineInstallError(f"The {item.label} download failed its SHA-256 check; it was not installed.")
    base = _engine_dir(engine, root)
    base.mkdir(parents=True, exist_ok=True)
    try:
        staging = extract_to_staging(archive, base / ".staging")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise EngineInstallError(f"The {item.label} package could not be unpacked safely: {exc}") from exc
    try:
        package = _package_root(staging)
        manifest = check_manifest(_read_manifest(package), engine, selected, compatible)
        expected = {PurePosixPath(PACKAGE_FILE), PurePosixPath("bin") / manifest["filename"]}
        present = {PurePosixPath(path.relative_to(package).as_posix())
                   for path in package.rglob("*") if path.is_file()}
        if present != expected:
            raise EngineInstallError(f"The {item.label} package contains unexpected files.")
        if verify_binary(package, manifest) is not None:
            raise EngineInstallError(f"The {item.label} file in the download is damaged.")
        binary = package / "bin" / manifest["filename"]
        if os.name == "posix":
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if cancelled():
            raise EngineInstallError(f"{item.label} installation was cancelled.")
        problem = probe(binary, manifest)
        if problem:
            raise EngineInstallError(problem)
        install_id = install_id_for(manifest)
        previous = installed(engine, root=root, platform=selected)
        target = base / install_id
        if target.is_dir() and verify_binary(target, manifest) is None:
            _write_current(base, install_id, server_build_id)
            return EngineInstallResult(installed(engine, root=root, platform=selected), already_installed=True)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            if target.exists():
                raise EngineInstallError(f"An older copy of the {item.label} is still in use. "
                                         "Close Live Simulations and bakes, then try again.")
        _move_into_place(package, target)
        _write_current(base, install_id, server_build_id)
        keep = {install_id, ".staging"} | ({previous.install_id} if previous is not None else set())
        _prune(base, keep)
        return EngineInstallResult(installed(engine, root=root, platform=selected))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def build_package(engine: str, binary: Path, output: Path, *, version: str, build_id: str,
                  commit: str = "", platform: str | None = None, extra: dict | None = None) -> dict:
    """Write an engine package ZIP (release tooling). Returns its engine.json."""
    item = spec(engine)
    selected = platform or current_platform()
    filename = item.filename(selected)
    if filename is None:
        raise ValueError(f"{item.label} has no {selected} build")
    data = Path(binary).read_bytes()
    manifest = {"kind": PACKAGE_KIND, "schema_version": 1, "engine": engine, "version": version,
                "build_id": build_id, "commit": commit, "platform": selected, "filename": filename,
                "file_size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **(extra or {})}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(PACKAGE_FILE, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        info = zipfile.ZipInfo(f"bin/{filename}", date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100755 << 16)
        bundle.writestr(info, data)
    return manifest


__all__ = ["AETHER", "TETHYS", "SPECS", "EngineInstallError", "EngineInstallResult", "InstalledEngine",
           "build_package", "check_manifest", "choose_build", "engines_root", "install_package", "installed", "spec",
           "verify_binary"]
