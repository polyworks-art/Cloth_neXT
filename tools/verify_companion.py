# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the ignored development EXE connects and shuts down cleanly."""

from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.bake.status import BakeSnapshot, BakeState  # noqa: E402
from cloth_next.bake.transport import LocalSocketServer  # noqa: E402
from cloth_next.provenance.worker_protocol import (  # noqa: E402
    receive_message,
    send_message,
)


def verify_threadmark_worker(exe: Path) -> None:
    from PIL import Image

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(15)
    token = secrets.token_hex(32)
    process = subprocess.Popen(
        [
            str(exe),
            "--mode",
            "threadmark-worker",
            "--host",
            "127.0.0.1",
            "--port",
            str(listener.getsockname()[1]),
            "--token",
            token,
        ],
        shell=False,
    )
    try:
        connection, address = listener.accept()
        with connection:
            connection.settimeout(30)
            if address[0] != "127.0.0.1":
                raise RuntimeError("ThreadMark worker was not loopback")
            if receive_message(connection) != {"type": "ready", "token": token}:
                raise RuntimeError("ThreadMark worker did not authenticate")
            with tempfile.TemporaryDirectory(
                prefix="clothnext-threadmark-exe-"
            ) as root:
                path = Path(root) / "render.png"
                image = Image.new("RGB", (640, 512), (38, 92, 156))
                image.save(path)
                image.close()
                before = path.read_bytes()
                send_message(
                    connection,
                    {"type": "encode", "token": token, "path": str(path)},
                )
                response = receive_message(connection)
                if response != {"ok": True, "reason": ""}:
                    raise RuntimeError(f"ThreadMark worker rejected encode: {response}")
                if path.read_bytes() == before:
                    raise RuntimeError("ThreadMark worker did not change the image")
            send_message(connection, {"type": "shutdown", "token": token})
            if receive_message(connection) != {"ok": True}:
                raise RuntimeError("ThreadMark worker rejected shutdown")
        process.wait(timeout=10)
        if process.returncode:
            raise RuntimeError(f"ThreadMark worker exited {process.returncode}")
    finally:
        listener.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)


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
        completed = subprocess.run(
            [str(exe), *arguments], env=env, timeout=15, check=False, shell=False
        )
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
        verify_threadmark_worker(exe)
        print("Development EXE: Bake, information, and ThreadMark modes passed")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        server.close()


if __name__ == "__main__":
    main()
