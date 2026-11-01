# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER Engine (native) discovery, validation and error mapping (pure, no bpy).

The native AETHER Engine is a separate backend from the PPF-based solvers
(including the historical upstream PPF build that the solver dropdown calls
"AETHER"); it never shares installations, caches or recovery data with them.

The engine ships as ``bin/AETHER Engine.exe`` authenticated by
``aether_engine_manifest.json`` (size, SHA-256, protocol and API version),
following the TETHYS Engine convention. ``CLOTH_NEXT_AETHER_ENGINE`` points a
development build at an engine outside the extension (developer override).
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


def locate(root: Path | None = None, *, platform: str | None = None) -> tuple[Path, bool]:
    """(authenticated engine path, developer override?). Raises EngineUnavailable."""
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
    root = Path(root) if root is not None else extension_root()
    try:
        payload = json.loads((root / MANIFEST).read_text("utf-8"))
    except FileNotFoundError:
        raise EngineUnavailable("The AETHER Engine is not included in this Cloth NeXt build",
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
    return binary, False


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
        manifest = json.loads((extension_root() / MANIFEST).read_text("utf-8"))
        if manifest.get("build_id") != identity.build_id:
            raise EngineUnavailable("The AETHER Engine build does not match its manifest. Reinstall Cloth NeXt",
                                    "CNX-E132")
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
