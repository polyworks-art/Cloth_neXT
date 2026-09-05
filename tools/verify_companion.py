# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the ignored development EXE connects and shuts down cleanly."""

from pathlib import Path
import subprocess
import sys
import time
import tomllib
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.bake.status import BakeSnapshot, BakeState  # noqa: E402
from cloth_next.bake.transport import LocalSocketServer  # noqa: E402
def verify_information_modes(exe: Path) -> None:
    env = dict(__import__("os").environ)
    env["CLOTH_NEXT_COMPANION_AUTO_CLOSE_MS"] = "350"
    version = tomllib.loads(
        (ROOT / "cloth_next" / "blender_manifest.toml").read_text(encoding="utf-8")
    )["version"]
    content = str(ROOT / "cloth_next" / "resources" / "onboarding")
    for arguments in (
        ("--mode", "welcome", "--content-root", content),
        ("--mode", "whats-new", "--version", version, "--content-root", content),
    ):
        with tempfile.TemporaryDirectory(prefix="clothnext-info-smoke-") as temporary:
            ready = Path(temporary) / "ready"
            env["CLOTH_NEXT_INFO_READY_PATH"] = str(ready)
            env["CLOTH_NEXT_INFO_READY_TOKEN"] = "smoke-ready"
            completed = subprocess.run(
                [str(exe), *arguments], env=env, timeout=15, check=False, shell=False
            )
            if not ready.is_file() or ready.read_text(encoding="utf-8") != "smoke-ready":
                raise RuntimeError("Companion never acknowledged its visible window")
        if completed.returncode:
            raise RuntimeError(
                f"companion information mode failed ({arguments}, "
                f"exit {completed.returncode})"
            )
    invalid = subprocess.run(
        [
            str(exe),
            "--mode",
            "whats-new",
            "--version",
            "invalid",
            "--content-root",
            content,
        ],
        env=env,
        timeout=15,
        check=False,
        shell=False,
    )
    if invalid.returncode == 0:
        raise RuntimeError("companion accepted an invalid What's-New version")


def main():
    exe = ROOT / "companion/dist/Cloth NeXt Bake.exe"
    if not exe.is_file():
        raise SystemExit("development EXE missing")
    server = LocalSocketServer()
    process = subprocess.Popen(
        [str(exe), "--port", str(server.port), "--token", server.token]
    )
    try:
        end = time.time() + 15
        ready = False
        while time.time() < end and not ready:
            ready = server.poll_request() == "ready"
            time.sleep(0.05)
        if not ready:
            raise RuntimeError("companion did not authenticate")
        server.publish(
            BakeSnapshot(
                state=BakeState.SIMULATING,
                preview=True,
                progress_current=38,
                progress_total=120,
            )
        )
        server.shutdown_companion()
        process.wait(timeout=10)
        if process.returncode:
            raise RuntimeError(f"companion exited {process.returncode}")
        verify_information_modes(exe)
        print("Development EXE: Bake and information modes passed")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        server.close()


if __name__ == "__main__":
    main()
