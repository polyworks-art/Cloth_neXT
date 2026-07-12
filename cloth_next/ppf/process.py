"""Owned local PPF process lifecycle; never controls external servers."""

from __future__ import annotations

import queue
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import gettempdir
from typing import Mapping

from cloth_next.core.errors import ClothNextError, ErrorCategory, ErrorRecord
from cloth_next.core.logging import get_logger, log_with_context
from .compatibility import parse_executable_version
from .models import ConnectionOwnership
from .progress import ProgressSnapshot, read_progress


@dataclass(frozen=True, slots=True)
class SolverProcessConfig:
    executable_path: Path
    working_directory: Path
    host: str = "127.0.0.1"
    port: int = 9090
    progress_file: Path | None = None
    debug: bool = False
    startup_timeout: float = 20.0
    connect_timeout: float = 2.0
    read_timeout: float = 2.0
    shutdown_timeout: float = 5.0
    ownership_mode: ConnectionOwnership = ConnectionOwnership.OWNED_PROCESS
    environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        executable = self.executable_path.expanduser().resolve()
        workdir = self.working_directory.expanduser().resolve()
        progress = (self.progress_file.expanduser().resolve() if self.progress_file else
                    Path(gettempdir()).resolve() / "cloth-next" / f"progress-{uuid.uuid4().hex}.log")
        object.__setattr__(self, "executable_path", executable)
        object.__setattr__(self, "working_directory", workdir)
        object.__setattr__(self, "progress_file", progress)
        if not executable.is_file():
            raise ValueError(f"solver executable is not a file: {executable}")
        if not workdir.is_dir():
            raise ValueError(f"working directory is not a directory: {workdir}")
        if not progress.is_absolute():
            raise ValueError("progress file must be absolute")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if not self.host.strip():
            raise ValueError("host must not be empty")
        if min(self.startup_timeout, self.connect_timeout, self.read_timeout, self.shutdown_timeout) <= 0:
            raise ValueError("timeouts must be positive")

    def arguments(self) -> list[str]:
        args = [str(self.executable_path), "--host", self.host, "--port", str(self.port),
                "--progress-file", str(self.progress_file)]
        if self.debug:
            args.append("--debug")
        return args

    def subprocess_environment(self) -> dict[str, str]:
        result = os.environ.copy()
        result.update(dict(self.environment))
        return result


@dataclass(frozen=True, slots=True)
class ProcessPoll:
    running: bool
    process_id: int | None
    exit_code: int | None
    stdout_tail: tuple[str, ...] = ()
    stderr_tail: tuple[str, ...] = ()
    progress: ProgressSnapshot = field(default_factory=lambda: ProgressSnapshot(False, False, ()))


class SolverProcessManager:
    def __init__(self, config: SolverProcessConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[tuple[str, str]] = queue.Queue()
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._threads: list[threading.Thread] = []
        self._logger = get_logger("ppf.process")

    @property
    def ownership(self) -> ConnectionOwnership:
        return self.config.ownership_mode

    def executable_version(self) -> tuple[str, str, str]:
        try:
            result = subprocess.run(
                [str(self.config.executable_path), "--version"],
                cwd=self.config.working_directory, capture_output=True, text=True,
                timeout=self.config.connect_timeout, check=True, shell=False,
                env=self.config.subprocess_environment(),
            )
            return parse_executable_version(result.stdout or result.stderr)
        except (subprocess.SubprocessError, OSError, ValueError) as exc:
            raise ClothNextError(ErrorRecord.create(
                category=ErrorCategory.SOLVER_INSTALLATION,
                user_message="The configured executable is not the required PPF solver build.",
                technical_message=f"ppf-cts-server --version failed: {exc}",
                recommended_action="Configure ppf-cts-server.exe built from pinned commit 7193f158.",
                recoverable=True,
                exception=exc,
            )) from exc

    def start(self) -> None:
        if self.ownership is not ConnectionOwnership.OWNED_PROCESS:
            raise PermissionError("external server ownership cannot start a process")
        if self._process is not None and self._process.poll() is None:
            raise RuntimeError("solver process is already running")
        self.config.progress_file.parent.mkdir(parents=True, exist_ok=True)
        sanitized = [self.config.executable_path.name, "--host", self.config.host,
                     "--port", str(self.config.port), "--progress-file", "<instance-progress-file>"]
        if self.config.debug:
            sanitized.append("--debug")
        log_with_context(self._logger, 20, "process start attempt",
                         {"host": self.config.host, "port": self.config.port, "arguments": sanitized})
        try:
            self._process = subprocess.Popen(
                self.config.arguments(), cwd=self.config.working_directory,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                bufsize=1, shell=False, env=self.config.subprocess_environment(),
            )
        except OSError as exc:
            raise ClothNextError(ErrorRecord.create(
                category=ErrorCategory.SOLVER_INSTALLATION,
                user_message="The PPF solver process could not be started.",
                technical_message=f"Popen failed: {exc}",
                recommended_action="Verify the executable, permissions, and solver installation.",
                recoverable=True,
                exception=exc,
            )) from exc
        log_with_context(self._logger, 20, "process started", {"process_id": self._process.pid})
        self._threads = []
        for label, stream in (("stdout", self._process.stdout), ("stderr", self._process.stderr)):
            assert stream is not None
            thread = threading.Thread(target=self._read_stream, args=(label, stream), name=f"cloth-next-ppf-{label}", daemon=False)
            thread.start()
            self._threads.append(thread)

    def _read_stream(self, label: str, stream: object) -> None:
        try:
            for line in stream:  # type: ignore[union-attr]
                self._lines.put((label, line.rstrip()))
        finally:
            stream.close()  # type: ignore[union-attr]

    def _drain(self) -> None:
        while True:
            try:
                label, line = self._lines.get_nowait()
            except queue.Empty:
                break
            target = self._stdout if label == "stdout" else self._stderr
            target.append(line)
            del target[:-100]

    def poll(self) -> ProcessPoll:
        self._drain()
        code = None if self._process is None else self._process.poll()
        return ProcessPoll(
            running=self._process is not None and code is None,
            process_id=None if self._process is None else self._process.pid,
            exit_code=code, stdout_tail=tuple(self._stdout[-40:]), stderr_tail=tuple(self._stderr[-40:]),
            progress=read_progress(self.config.progress_file),
        )

    def stop(self) -> ProcessPoll:
        if self.ownership is not ConnectionOwnership.OWNED_PROCESS:
            raise PermissionError("external server must never be stopped by Cloth NeXt")
        process = self._process
        if process is None:
            return self.poll()
        log_with_context(self._logger, 20, "shutdown attempt", {"process_id": process.pid})
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=self.config.shutdown_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=self.config.shutdown_timeout)
        else:
            process.wait()
        for thread in self._threads:
            thread.join(timeout=self.config.shutdown_timeout)
        if any(thread.is_alive() for thread in self._threads):
            raise RuntimeError("process reader thread did not stop")
        result = self.poll()
        log_with_context(self._logger, 20, "shutdown result", {"exit_code": result.exit_code})
        self._process = None
        self._threads.clear()
        return result

    def restart(self) -> None:
        if self.ownership is not ConnectionOwnership.OWNED_PROCESS:
            raise PermissionError("external server cannot be restarted")
        self.stop()
        self.start()

    def early_exit_error(self, poll: ProcessPoll) -> ClothNextError:
        return ClothNextError(ErrorRecord.create(
            category=ErrorCategory.SOLVER_INSTALLATION,
            user_message="The PPF solver exited before it became ready.",
            technical_message=f"exit_code={poll.exit_code}; stderr_tail={poll.stderr_tail}; progress_tail={poll.progress.tail}",
            recommended_action="Inspect the solver and Cloth NeXt logs, verify CUDA requirements, and retry.",
            recoverable=True,
            context={"exit_code": poll.exit_code},
        ))
