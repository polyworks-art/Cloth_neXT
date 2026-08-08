import json
from pathlib import Path
import threading

import pytest

from cloth_next.newton_preview import install


def test_managed_install_publishes_pointer_only_after_version_cuda_probe(
        tmp_path, monkeypatch):
    paths = install.NewtonInstallPaths(tmp_path / "newton")
    paths.python.parent.mkdir(parents=True)
    paths.python.write_bytes(b"python")
    calls = []

    def run(arguments, **_kwargs):
        calls.append(tuple(map(str, arguments)))
        if "-c" in arguments:
            return json.dumps({"newton": "1.4.0", "warp": "1.15.0",
                               "pytetwild": "0.3.0",
                               "cuda": "RTX Test"}) + "\n"
        return ""

    monkeypatch.setattr(install, "_run_owned", run)
    result = install.install(cancel_event=threading.Event(), paths=paths)

    assert result == paths.python
    assert any("newton==1.4.0" in call for call in calls)
    assert any("warp-lang==1.15.0" in call for call in calls)
    assert any("pytetwild==0.3.0" in call for call in calls)
    assert install.read_current(paths) == paths.python.resolve()
    metadata = json.loads(paths.current_json.read_text(encoding="utf-8"))
    assert metadata["codename"] == "Principia"
    assert metadata["cuda_device"] == "RTX Test"
    assert metadata["pytetwild_version"] == "0.3.0"


def test_failed_probe_never_publishes_current_environment(tmp_path, monkeypatch):
    paths = install.NewtonInstallPaths(tmp_path / "newton")
    paths.python.parent.mkdir(parents=True)
    paths.python.write_bytes(b"python")

    def run(arguments, **_kwargs):
        if "-c" in arguments:
            return json.dumps({"newton": "wrong", "warp": "1.15.0",
                               "cuda": "RTX Test"})
        return ""

    monkeypatch.setattr(install, "_run_owned", run)
    with pytest.raises(RuntimeError, match="version or CUDA"):
        install.install(cancel_event=threading.Event(), paths=paths)
    assert not paths.current_json.exists()
    assert install.read_current(paths) is None


def test_current_pointer_rejects_paths_outside_managed_root(tmp_path):
    paths = install.NewtonInstallPaths(tmp_path / "newton")
    paths.current_json.parent.mkdir(parents=True)
    outside = tmp_path / "unowned" / "python.exe"
    outside.parent.mkdir(); outside.write_bytes(b"python")
    paths.current_json.write_text(json.dumps({
        "schema": 1, "release_id": install.RELEASE_ID,
        "python": str(outside)}), encoding="utf-8")
    assert install.read_current(paths) is None


def test_configured_bootstrap_python_must_exist(monkeypatch, tmp_path):
    missing = tmp_path / "missing-python.exe"
    monkeypatch.setenv("CLOTHNEXT_NEWTON_BOOTSTRAP_PYTHON", str(missing))
    with pytest.raises(FileNotFoundError):
        install.bootstrap_command()
