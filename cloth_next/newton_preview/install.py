# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit managed Newton environment outside Blender and the extension."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from .contracts import NEWTON_VERSION, WARP_VERSION

RELEASE_ID = f"newton-{NEWTON_VERSION}-warp-{WARP_VERSION}"
CODENAME = "Principia"


@dataclass(frozen=True)
class NewtonInstallPaths:
    root: Path

    @classmethod
    def default(cls):
        if os.name == "nt":
            root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        else:
            root = Path.home() / ".local/share"
        return cls(root / "ClothNeXt/newton")

    @property
    def version_root(self):
        return self.root / "versions" / RELEASE_ID

    @property
    def python(self):
        suffix = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
        return self.version_root / "venv" / suffix

    @property
    def current_json(self):
        return self.root / "current.json"


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_current(paths=None) -> Path | None:
    paths = paths or NewtonInstallPaths.default()
    try:
        value = json.loads(paths.current_json.read_text(encoding="utf-8"))
        if value.get("schema") != 1 or value.get("release_id") != RELEASE_ID:
            return None
        python = Path(value["python"]).resolve()
        root = paths.root.resolve()
        if root not in python.parents or python != paths.python.resolve():
            return None
        return python if python.is_file() else None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def bootstrap_command() -> list[str]:
    configured = os.environ.get("CLOTHNEXT_NEWTON_BOOTSTRAP_PYTHON", "").strip()
    if configured:
        executable = Path(configured).expanduser().resolve()
        if not executable.is_file():
            raise FileNotFoundError(f"Configured Newton bootstrap Python is missing: {executable}")
        return [str(executable)]
    if os.name == "nt":
        launcher = shutil.which("py")
        if launcher:
            return [launcher, "-3.11"]
    executable = shutil.which("python3.11")
    if executable:
        return [executable]
    raise FileNotFoundError(
        "Python 3.11 is required outside Blender. Install Python 3.11 or set "
        "CLOTHNEXT_NEWTON_BOOTSTRAP_PYTHON.")


def _run_owned(arguments, *, timeout, cancel_event, process_callback,
               environment=None):
    process = subprocess.Popen(
        [str(value) for value in arguments], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, shell=False, env=environment)
    process_callback(process)
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        if cancel_event.is_set():
            process.terminate()
            try: process.wait(timeout=2.0)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2.0)
            raise RuntimeError("Newton installation cancelled")
        if time.monotonic() >= deadline:
            process.terminate()
            try: process.wait(timeout=2.0)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2.0)
            raise TimeoutError("Newton installation step timed out")
        time.sleep(0.1)
    stdout, stderr = process.communicate(timeout=5.0)
    process_callback(None)
    if process.returncode:
        details = (stderr or stdout or "external Python failed")[-8192:]
        raise RuntimeError(details)
    return stdout


def install(*, cancel_event: threading.Event, process_callback=lambda _p: None,
            status_callback=lambda _s: None, paths=None) -> Path:
    paths = paths or NewtonInstallPaths.default()
    paths.version_root.mkdir(parents=True, exist_ok=True)
    if not paths.python.is_file():
        status_callback("Creating external Python environment")
        _run_owned([*bootstrap_command(), "-m", "venv",
                    str(paths.version_root / "venv")], timeout=180.0,
                   cancel_event=cancel_event, process_callback=process_callback)
    status_callback("Downloading Newton · Principia")
    _run_owned([
        str(paths.python), "-m", "pip", "install",
        "--disable-pip-version-check", "--no-input",
        f"newton=={NEWTON_VERSION}", f"warp-lang=={WARP_VERSION}"],
        timeout=900.0, cancel_event=cancel_event,
        process_callback=process_callback)
    status_callback("Verifying Newton and CUDA")
    probe = (
        "import json,newton,warp as wp; wp.config.log_level=wp.LOG_WARNING; wp.init(); "
        "d=next((d for d in wp.get_devices() if d.is_cuda),None); "
        "print(json.dumps({'newton':newton.__version__,'warp':wp.__version__,"
        "'cuda':d.name if d else None}))")
    output = _run_owned([str(paths.python), "-c", probe], timeout=120.0,
                        cancel_event=cancel_event,
                        process_callback=process_callback)
    value = json.loads(output.strip().splitlines()[-1])
    if (value.get("newton") != NEWTON_VERSION
            or value.get("warp") != WARP_VERSION or not value.get("cuda")):
        raise RuntimeError("Installed Newton environment failed version or CUDA verification")
    _atomic_json(paths.current_json, {
        "schema": 1, "release_id": RELEASE_ID, "codename": CODENAME,
        "newton_version": NEWTON_VERSION, "warp_version": WARP_VERSION,
        "python": str(paths.python.resolve()), "cuda_device": value["cuda"],
    })
    status_callback("Ready")
    return paths.python
