"""Probe recovery TCMD spellings against a real local PPF server.

This intentionally uses a non-existent project.  A parsed state-machine
response proves that the command spelling is supported; a protocol/parser
error proves that it is not.  No solver project is created or modified.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def request(port: int, payload: str) -> dict:
    raw = payload.encode("utf-8")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(b"TCMD" + len(raw).to_bytes(4, "big") + raw)
        chunks = bytearray()
        while b"\n" not in chunks:
            part = sock.recv(65536)
            if not part:
                break
            chunks.extend(part)
    return json.loads(bytes(chunks).split(b"\n", 1)[0])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True)
    args = parser.parse_args()
    port = free_port()
    process = subprocess.Popen(
        [args.executable, "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                status = request(port, "--name clothnext_recovery_probe")
                break
            except (OSError, ValueError, json.JSONDecodeError):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("solver did not become ready")
                time.sleep(0.05)
        results = {"status": status}
        commands = {
            "resume_latest": "--request resume",
            "resume_frame": "--request resume --frame 20",
            "save_and_quit": "--request save_and_quit",
            "update_params": "--request update_params",
        }
        for name, payload in commands.items():
            try:
                results[name] = request(
                    port, f"--name clothnext_probe_{name} {payload}")
            except Exception as exc:  # probe must report every capability
                results[name] = {"probe_error": str(exc)}
        project_names = ["clothnext_recovery_probe"]
        project_names.extend(f"clothnext_probe_{name}" for name in commands)
        for project_name in project_names:
            try:
                request(port, f"--name {project_name} --request delete")
            except Exception:
                pass
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
