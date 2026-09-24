# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Choose a verified multi-backend release's native server for this host."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

from .layout import EXECUTABLE_NAME
from ..platform_support import platform_spec

WORKER_NAME = platform_spec().solver_worker_filename

_WINDOWS_LOADER_FAILURE = {3221225781, 3221225785, 3221225794}
BACKEND_CHOICES = ("AUTO", "CUDA", "ROCM", "CPU")


def available_backend_choices(protocol_version: str, root: Path) -> tuple[str, ...]:
    """Return packaged choices for the selected verified generation."""
    if protocol_version == "0.22" and (
            root / "target" / "cpu" / "release" / EXECUTABLE_NAME).is_file():
        return tuple(choice for choice in BACKEND_CHOICES
                     if choice == "AUTO" or (
                         root / "target" / choice.lower() / "release" /
                         EXECUTABLE_NAME).is_file())
    if protocol_version == "0.18" and (
            root / "target" / "release" / EXECUTABLE_NAME).is_file():
        return ("AUTO", "CUDA")
    return ("AUTO",)


def executable_for_choice(root: Path, protocol_version: str,
                          choice: str) -> Path:
    """Resolve a backend without substituting another for an explicit choice."""
    if choice not in BACKEND_CHOICES:
        raise ValueError(f"unknown solver backend choice {choice!r}")
    available = available_backend_choices(protocol_version, root)
    if choice not in available:
        raise ValueError(f"{choice} is not available in the selected solver release")
    if choice == "AUTO":
        return (preferred_executable(root) if protocol_version == "0.22"
                else root / "target" / "release" / EXECUTABLE_NAME)
    if protocol_version == "0.18":
        return root / "target" / "release" / EXECUTABLE_NAME
    backend = choice.lower()
    candidate = root / "target" / backend / "release" / EXECUTABLE_NAME
    worker = candidate.with_name(WORKER_NAME)
    if not worker.is_file():
        raise ValueError(f"{choice} solver worker is missing")
    environment = _probe_environment(root)
    try:
        probe = subprocess.run([str(worker), "--probe"],
                               capture_output=True, text=True,
                               timeout=120, env=environment, shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"{choice} backend probe could not run: {exc}") from exc
    if probe.returncode != 0 or f"backend: {backend}" not in probe.stdout:
        raise ValueError(f"{choice} backend is unavailable on this machine")
    return candidate


def _probe_environment(root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    library_dirs = [root / "bin", root / "python", root / "python" / "Scripts"]
    cuda_path = environment.get("CUDA_PATH")
    if cuda_path:
        library_dirs.append(Path(cuda_path) / "bin")
    environment["PATH"] = os.pathsep.join(
        [str(path) for path in library_dirs if path.is_dir()]
        + [environment.get("PATH", "")])
    return environment


def selected_backend(root: Path, executable: Path) -> tuple[str, Path] | None:
    """Identify a pinned official backend from its exact server location."""
    root = root.resolve()
    executable = executable.resolve()
    for backend in ("cuda", "rocm", "cpu"):
        target = root / "target" / backend
        if executable == target / "release" / EXECUTABLE_NAME:
            return backend, target
    return None


def verify_backend_status(identity: tuple[str, Path],
                          response: dict[str, object]) -> None:
    """Reject a Gaia server whose actual worker differs from the pinned build."""
    backend, target = identity
    actual_backend = response.get("solver_backend")
    actual_target = response.get("solver_target_dir")
    if (actual_backend != backend or not isinstance(actual_target, str)
            or not actual_target or Path(actual_target).resolve() != target):
        raise ValueError(
            "selected solver backend mismatch: "
            f"expected=({backend!r}, {str(target)!r}), "
            f"actual=({actual_backend!r}, {actual_target!r})")


def preferred_executable(root: Path) -> Path:
    """Use verified CUDA when available, otherwise the verified CPU build.

    The official ROCm binary remains installed but is not an automatic
    production choice until a real Cloth NeXt AMD Bake is certified.
    """
    signatures = []
    for backend in ("cuda", "rocm", "cpu"):
        worker = root / "target" / backend / "release" / WORKER_NAME
        try:
            stat = worker.stat()
            signatures.append((backend, stat.st_size, stat.st_mtime_ns))
        except OSError:
            signatures.append((backend, 0, 0))
    return _preferred_executable_cached(root, tuple(signatures))


@lru_cache(maxsize=16)
def _preferred_executable_cached(root: Path, _signatures: tuple) -> Path:
    target = root / "target"
    cpu = target / "cpu" / "release" / EXECUTABLE_NAME
    if not cpu.is_file():
        raise ValueError("multi-backend release lacks its CPU server")
    environment = _probe_environment(root)
    for backend in ("cuda",):
        candidate = target / backend / "release" / EXECUTABLE_NAME
        worker = candidate.with_name(WORKER_NAME)
        if not candidate.is_file() or not worker.is_file():
            continue
        try:
            probe = subprocess.run([str(worker), "--probe"],
                                   capture_output=True, text=True,
                                   timeout=120, env=environment, shell=False)
        except OSError:
            continue
        if probe.returncode == 0 and f"backend: {backend}" in probe.stdout:
            return candidate
        if (probe.returncode not in (3, *_WINDOWS_LOADER_FAILURE)
                and probe.returncode & 0xffffffff not in _WINDOWS_LOADER_FAILURE):
            raise ValueError(f"{backend} backend probe failed unexpectedly")
    return cpu
