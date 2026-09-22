# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Backend identity and probe-order checks for official multi-backend layouts."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.ppf import backend_selection


def _tree(root: Path):
    for backend in ("cuda", "rocm", "cpu"):
        release = root / "target" / backend / "release"
        release.mkdir(parents=True)
        (release / "ppf-cts-server.exe").touch()
        (release / "ppf-contact-solver.exe").touch()


def test_selected_backend_uses_exact_server_location(tmp_path):
    _tree(tmp_path)
    for backend in ("cuda", "rocm", "cpu"):
        exe = tmp_path / "target" / backend / "release" / "ppf-cts-server.exe"
        assert backend_selection.selected_backend(tmp_path, exe) == (
            backend, tmp_path / "target" / backend)
    assert backend_selection.selected_backend(
        tmp_path, tmp_path / "target" / "unknown" / "release" /
        "ppf-cts-server.exe") is None


def test_preference_uses_cpu_when_cuda_has_no_device(tmp_path, monkeypatch):
    _tree(tmp_path)
    calls = []
    def probe(args, **_kwargs):
        backend = Path(args[0]).parents[1].name
        calls.append(backend)
        if backend == "cuda":
            return SimpleNamespace(returncode=3, stdout="backend: cuda\n")
        return SimpleNamespace(returncode=0, stdout="backend: rocm\n")
    monkeypatch.setattr(backend_selection.subprocess, "run", probe)
    backend_selection._preferred_executable_cached.cache_clear()
    assert backend_selection.preferred_executable(tmp_path) == (
        tmp_path / "target" / "cpu" / "release" / "ppf-cts-server.exe")
    assert calls == ["cuda"]


def test_unexpected_probe_failure_is_not_silent_fallback(tmp_path, monkeypatch):
    _tree(tmp_path)
    monkeypatch.setattr(backend_selection.subprocess, "run",
                        lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=9, stdout=""))
    backend_selection._preferred_executable_cached.cache_clear()
    with pytest.raises(ValueError, match="cuda backend probe failed"):
        backend_selection.preferred_executable(tmp_path)


def test_backend_status_must_match_explicit_build(tmp_path):
    _tree(tmp_path)
    identity = ("cuda", tmp_path / "target" / "cuda")
    backend_selection.verify_backend_status(identity, {
        "solver_backend": "cuda", "solver_target_dir": str(identity[1])})
    for response in (
        {"solver_backend": "cpu", "solver_target_dir": str(identity[1])},
        {"solver_backend": "cuda", "solver_target_dir": str(tmp_path / "target" / "cpu")},
        {"solver_backend": "future", "solver_target_dir": str(identity[1])},
        {"solver_backend": "cuda", "solver_target_dir": ""},
    ):
        with pytest.raises(ValueError, match="backend mismatch"):
            backend_selection.verify_backend_status(identity, response)


def test_explicit_choice_uses_exact_build_and_never_falls_back(tmp_path, monkeypatch):
    _tree(tmp_path)
    calls = []
    def probe(args, **_kwargs):
        backend = Path(args[0]).parents[1].name
        calls.append(backend)
        return SimpleNamespace(returncode=0, stdout=f"backend: {backend}\n")
    monkeypatch.setattr(backend_selection.subprocess, "run", probe)
    for choice in ("CUDA", "ROCM", "CPU"):
        assert backend_selection.executable_for_choice(
            tmp_path, "0.22", choice) == (
                tmp_path / "target" / choice.lower() / "release" /
                "ppf-cts-server.exe")
    assert calls == ["cuda", "rocm", "cpu"]
    monkeypatch.setattr(backend_selection.subprocess, "run",
                        lambda *_args, **_kwargs:
                        SimpleNamespace(returncode=3, stdout=""))
    with pytest.raises(ValueError, match="ROCM backend is unavailable"):
        backend_selection.executable_for_choice(tmp_path, "0.22", "ROCM")


def test_generation_availability_and_unknown_choice(tmp_path):
    _tree(tmp_path)
    assert backend_selection.available_backend_choices("0.22", tmp_path) == (
        "AUTO", "CUDA", "ROCM", "CPU")
    (tmp_path / "target" / "rocm" / "release" / "ppf-cts-server.exe").unlink()
    assert "ROCM" not in backend_selection.available_backend_choices(
        "0.22", tmp_path)
    with pytest.raises(ValueError, match="not available"):
        backend_selection.executable_for_choice(tmp_path, "0.22", "ROCM")
    with pytest.raises(ValueError, match="unknown solver backend"):
        backend_selection.executable_for_choice(tmp_path, "0.22", "FUTURE")
    lumen = tmp_path / "lumen"
    (lumen / "target" / "release").mkdir(parents=True)
    (lumen / "target" / "release" / "ppf-cts-server.exe").touch()
    assert backend_selection.available_backend_choices("0.18", lumen) == (
        "AUTO", "CUDA")
    with pytest.raises(ValueError, match="not available"):
        backend_selection.executable_for_choice(lumen, "0.18", "CPU")
