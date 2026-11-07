# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER Engine (native) discovery, validation and error mapping (pure, no bpy).

The native AETHER Engine is a separate backend from the PPF-based solvers
(including the historical upstream PPF build that the solver dropdown calls
"AETHER"); it never shares installations, caches or recovery data with them.

The engine is installed from the PolyWorks server into the local engines
folder (``updater.native_engines``; no account needed) and authenticated there
by its package ``engine.json`` (size, SHA-256, protocol and API version). A
development ZIP may still carry a staged ``bin/AETHER Engine.exe`` with
``aether_engine_manifest.json``; it is used only when no engine is installed.
``CLOTH_NEXT_AETHER_ENGINE`` points a development build at any engine file
(developer override).
Nothing here starts a CUDA context: the engine runs in its own process.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from . import client

MANIFEST = "aether_engine_manifest.json"
FILENAMES = {"windows-x64": "AETHER Engine.exe"}
DEVELOPER_OVERRIDE = "CLOTH_NEXT_AETHER_ENGINE"
PROTOCOL_VERSION = client.PROTOCOL_VERSION
API_VERSION = client.API_VERSION
_VERSION_LINE = re.compile(r"^AETHER Engine (\S+) \(build ([0-9a-f]{16}), commit (\S+)\)$")


class EngineUnavailable(RuntimeError):
    """The engine is missing, damaged, incompatible or cannot run here."""

    def __init__(self, message: str, code: str = "CNX-E131"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EngineIdentity:
    path: Path
    version: str
    build_id: str
    commit: str
    developer_override: bool = False


def extension_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _platform() -> str:
    from ..platform_support import platform_spec
    return platform_spec().blender_platform


INSTALL_HINT = "Install it in Preferences > Add-ons > Cloth NeXt > Solver Engine"


def package_compatible(manifest: dict) -> str | None:
    """None when a server package fits this add-on, else the reason (install check)."""
    if manifest.get("protocol") != PROTOCOL_VERSION or list(manifest.get("api") or ()) != list(API_VERSION):
        return ("This AETHER Engine build does not match this Cloth NeXt version. "
                "Update Cloth NeXt or wait for a matching engine build.")
    if not isinstance(manifest.get("build_id"), str) or not re.fullmatch(r"[0-9a-f]{16}", manifest["build_id"]):
        return "The AETHER Engine package description is invalid."
    return None


def package_probe(binary: Path, manifest: dict) -> str | None:
    """None when the binary reports the version and build the package names."""
    try:
        identity = probe(binary)
    except EngineUnavailable as exc:
        return str(exc)
    if identity.build_id != manifest.get("build_id") or identity.version != manifest.get("version"):
        return "The AETHER Engine in the download does not match its description."
    return None


def _installed(platform: str):
    """(binary, manifest) of the engine installed from the server, or None."""
    from ..updater import native_engines
    item = native_engines.installed(native_engines.AETHER, platform=platform)
    if item is None:
        return None
    if package_compatible(item.manifest):
        raise EngineUnavailable("The installed AETHER Engine does not match this Cloth NeXt version. "
                                "Update it in Preferences > Add-ons > Cloth NeXt > Solver Engine", "CNX-E132")
    problem = native_engines.verify_binary(item.root, item.manifest)
    if problem:
        raise EngineUnavailable(f"The installed AETHER Engine is {problem}. Repair it in Preferences > "
                                "Add-ons > Cloth NeXt > Solver Engine", "CNX-E131")
    return item.binary, item.manifest


def installed_manifest() -> dict | None:
    """Package description of the engine ``locate()`` would use (no hashing)."""
    from ..updater import native_engines
    try:
        item = native_engines.installed(native_engines.AETHER, platform=_platform())
    except Exception:  # noqa: BLE001 - informational
        item = None
    if item is not None:
        return item.manifest
    try:
        return json.loads((extension_root() / MANIFEST).read_text("utf-8"))
    except (OSError, ValueError):
        return None


def locate(root: Path | None = None, *, platform: str | None = None) -> tuple[Path, bool]:
    """(authenticated engine path, developer override?). Raises EngineUnavailable.

    Order: developer override, the engine installed from the server, then a
    development ZIP's staged engine (``root`` names that extension folder)."""
    override = os.environ.get(DEVELOPER_OVERRIDE, "").strip()
    if override and root is None:
        path = Path(override)
        if not path.is_file():
            raise EngineUnavailable(f"{DEVELOPER_OVERRIDE} does not point to a file", "CNX-E131")
        return path, True
    selected = platform or _platform()
    filename = FILENAMES.get(selected)
    if filename is None:
        raise EngineUnavailable(f"The AETHER Engine is not available for {selected}", "CNX-E132")
    if root is None:
        found = _installed(selected)
        if found is not None:
            return found[0], False
    root = Path(root) if root is not None else extension_root()
    try:
        payload = json.loads((root / MANIFEST).read_text("utf-8"))
    except FileNotFoundError:
        raise EngineUnavailable(f"The AETHER Engine is not installed. {INSTALL_HINT}",
                                "CNX-E131") from None
    except (OSError, ValueError) as exc:
        raise EngineUnavailable("The AETHER Engine manifest is damaged. Reinstall Cloth NeXt",
                                "CNX-E131") from exc
    required = {"schema_version", "filename", "platform", "file_size", "sha256", "protocol", "api",
                "engine_version", "build_id"}
    if (not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != 1
            or payload["filename"] != filename or payload["platform"] != selected):
        raise EngineUnavailable("The AETHER Engine does not match this platform. Reinstall Cloth NeXt",
                                "CNX-E132")
    if payload["protocol"] != PROTOCOL_VERSION or list(payload["api"]) != list(API_VERSION):
        raise EngineUnavailable("The AETHER Engine version does not match this add-on. Reinstall Cloth NeXt",
                                "CNX-E132")
    binary = root / "bin" / filename
    try:
        digest = hashlib.sha256()
        size = 0
        with binary.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        raise EngineUnavailable("The AETHER Engine file is missing. Reinstall Cloth NeXt", "CNX-E131") from None
    if size != payload["file_size"] or digest.hexdigest() != payload["sha256"]:
        raise EngineUnavailable("The AETHER Engine file is damaged. Reinstall Cloth NeXt", "CNX-E131")
    return _resident(binary, payload["sha256"]), False


def _resident(binary: Path, sha256: str) -> Path:
    """The verified engine, run from a copy outside the extension folder.

    Blender renames the extension folder to install an update, and Windows refuses while an
    executable inside it runs (a Live Simulation or a bake keeps AETHER Engine.exe running). The copy
    is keyed by the manifest's hash; copies of other versions are removed when nothing runs them.
    Falls back to the bundled file when the copy cannot be made."""
    import shutil
    import uuid
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "ClothNeXt" / "aether"
    target = base / sha256[:16] / binary.name
    try:
        if not (target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha256):
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{binary.name}.{uuid.uuid4().hex}.tmp")
            try:
                shutil.copyfile(binary, temporary)
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != sha256:
                    return binary
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        for other in base.iterdir():
            if other.is_dir() and other.name != sha256[:16]:
                shutil.rmtree(other, ignore_errors=True)   # an older engine that is still running stays
        return target
    except OSError:
        return binary


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0


def probe(executable: Path, *, developer_override: bool = False, timeout: float = 15.0) -> EngineIdentity:
    """``--version`` of the engine (does not touch the GPU)."""
    try:
        result = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                                timeout=timeout, creationflags=_creation_flags())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineUnavailable(f"The AETHER Engine could not start ({exc.__class__.__name__})",
                                "CNX-E134") from exc
    match = _VERSION_LINE.match(result.stdout.strip())
    if result.returncode != 0 or match is None:
        raise EngineUnavailable("The AETHER Engine reported an unexpected version", "CNX-E132")
    return EngineIdentity(Path(executable), match.group(1), match.group(2), match.group(3),
                          developer_override)


def identify() -> EngineIdentity:
    path, override = locate()
    identity = probe(path, developer_override=override)
    if not override:
        manifest = installed_manifest() or {}
        if manifest.get("build_id") != identity.build_id:
            raise EngineUnavailable("The AETHER Engine build does not match its description. Repair it in "
                                    "Preferences > Add-ons > Cloth NeXt > Solver Engine", "CNX-E132")
    return identity


# ---------------------------------------------------------------- errors
# AETHER status names -> existing Cloth NeXt codes (core/error_codes.py) and
# an actionable artist message. No new CNX numbers are introduced.
STATUS_CODES = {
    "AE_ERROR_INVALID_ARGUMENT": ("CNX-E104", "The AETHER Engine rejected a scene value"),
    "AE_ERROR_INVALID_STATE": ("CNX-E169", "The AETHER Engine was in an unexpected state"),
    "AE_ERROR_NO_DEVICE": ("CNX-E133", "No usable NVIDIA CUDA GPU was found. The AETHER Engine needs one "
                                      "and never falls back to the CPU"),
    "AE_ERROR_DEVICE": ("CNX-E164", "The GPU reported an error"),
    "AE_ERROR_NUMERICAL": ("CNX-E165", "The simulation became numerically unstable"),
    "AE_ERROR_INTERNAL": ("CNX-E199", "The AETHER Engine failed internally"),
    "AE_ERROR_CHECKPOINT_CORRUPT": ("CNX-E129", "The AETHER recovery checkpoint is damaged"),
    "AE_ERROR_CHECKPOINT_INCOMPATIBLE": ("CNX-E129", "The AETHER recovery checkpoint does not match this "
                                                    "scene, preset, engine build or GPU"),
    "AE_ERROR_OUT_OF_MEMORY": ("CNX-E166", "Not enough GPU or system memory for this scene"),
    "AE_ERROR_API_VERSION": ("CNX-E132", "The AETHER Engine version does not match this add-on"),
    "AE_ERROR_DEVICE_LOST": ("CNX-E164", "The GPU was lost (driver reset or device fault)"),
}


def map_error(name: str, message: str = "") -> tuple[str, str]:
    """(CNX code, artist message) for an AETHER status name."""
    code, text = STATUS_CODES.get(name, ("CNX-E199", "The AETHER Engine failed"))
    detail = f": {message}" if message else ""
    return code, f"{text}{detail}"


def exit_reason(exit_code: int | None) -> tuple[str, str]:
    """(CNX code, artist message) for an engine process that ended."""
    if exit_code == 3:
        return map_error("AE_ERROR_DEVICE_LOST")
    if exit_code == 2:
        return "CNX-E145", "The AETHER Engine stopped after a protocol error"
    if exit_code == 5:
        return "CNX-E164", "The AETHER Engine stopped because Blender's process ended"
    return "CNX-E164", f"The AETHER Engine process ended unexpectedly (exit code {exit_code})"
