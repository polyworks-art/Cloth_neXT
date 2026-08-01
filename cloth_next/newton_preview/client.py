# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned external Newton worker process and non-blocking message queues."""

from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from .protocol import command_message, decode_message, encode_message
from ..ppf.process import _WindowsJob


class NewtonWorkerClient:
    def __init__(self, python_executable: Path, *, package_root: Path,
                 startup_timeout: float = 30.0):
        self.python_executable = Path(python_executable).resolve()
        self.package_root = Path(package_root).resolve()
        self.startup_timeout = float(startup_timeout)
        self.process = None
        self._reader = None
        self._stderr_reader = None
        self._messages = queue.Queue(maxsize=256)
        self._stderr = deque(maxlen=128)
        self._write_lock = threading.Lock()
        self._job = None

    @property
    def pid(self):
        return self.process.pid if self.process is not None else None

    def start(self) -> dict:
        if not self.python_executable.is_file():
            raise FileNotFoundError(f"Newton Python not found: {self.python_executable}")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.package_root)
        self.process = subprocess.Popen(
            [str(self.python_executable), "-m",
             "cloth_next.newton_preview.worker_main", "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(self.package_root), env=environment, shell=False, bufsize=0)
        try:
            self._job = _WindowsJob()
            self._job.assign(self.process)
        except Exception:
            self.process.terminate()
            self.process.wait(timeout=2.0)
            if self._job is not None:
                self._job.close()
            self._job = None
            raise
        self._reader = threading.Thread(target=self._read_stdout,
                                        name="clothnext-newton-stdout", daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr,
                                               name="clothnext-newton-stderr", daemon=True)
        self._reader.start(); self._stderr_reader.start()
        self.send("health")
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            message = self.poll(timeout=0.05)
            if message and message.get("event") == "health":
                if not message.get("ready"):
                    raise RuntimeError("Newton worker has no supported CUDA device")
                return message
            if self.process.poll() is not None:
                raise RuntimeError(self.failure_details())
        self.shutdown(grace=0.5)
        raise TimeoutError("Newton worker health check timed out")

    def _read_stdout(self):
        try:
            for raw in self.process.stdout:
                message = decode_message(raw)
                try:
                    self._messages.put(message, timeout=0.1)
                except queue.Full:
                    if message.get("event") == "result":
                        try: self._messages.get_nowait()
                        except queue.Empty: pass
                        self._messages.put_nowait(message)
        except Exception as exc:
            self._stderr.append(f"protocol reader failed: {exc}")

    def _read_stderr(self):
        for raw in self.process.stderr:
            self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())

    def send(self, command: str, **payload) -> None:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError(self.failure_details())
        message = encode_message(command_message(command, **payload))
        with self._write_lock:
            process.stdin.write(message)
            process.stdin.flush()

    def poll(self, timeout: float = 0.0):
        try:
            return self._messages.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def failure_details(self) -> str:
        code = self.process.poll() if self.process is not None else None
        tail = "\n".join(self._stderr)
        return f"Newton worker exited ({code}).{(' ' + tail) if tail else ''}"

    def shutdown(self, *, grace: float = 2.0) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try: self.send("shutdown")
            except (OSError, RuntimeError): pass
            try: process.wait(timeout=max(0.0, grace))
            except subprocess.TimeoutExpired:
                process.terminate()
                try: process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=2.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None: stream.close()
            except OSError:
                pass
        for thread in (self._reader, self._stderr_reader):
            if thread is not None: thread.join(timeout=1.0)
        if self._job is not None:
            self._job.close()
            self._job = None
        self.process = None
