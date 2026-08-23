# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Owned local PPF process lifecycle; never controls external servers."""

from __future__ import annotations

import queue
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import gettempdir
from typing import Mapping

from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord
from ..core.logging import get_logger, log_with_context
from ..core.safe_delete import delete_owned
from .compatibility import parse_executable_version
from .models import ConnectionOwnership
from .progress import ProgressSnapshot, read_progress


class _WindowsJob:
    """Own a spawned server and every descendant it creates.

    The official control server launches ``ppf-contact-solver.exe`` itself.
    A plain ``Popen`` handle therefore cannot stop the GPU worker after the
    server exits.  A kill-on-close Job Object gives Cloth NeXt one bounded
    ownership boundary without discovering or redistributing solver binaries.
    """

    def __init__(self) -> None:
        self._handle = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = (
            ctypes.c_void_p, wintypes.LPCWSTR)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (
            wintypes.HANDLE, wintypes.HANDLE)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD))
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        self._handle = handle
        self._kernel32 = kernel32

    def assign(self, process: subprocess.Popen[str]) -> None:
        if self._handle is None:
            return
        import ctypes
        raw_handle = getattr(process, "_handle", None)
        if not isinstance(raw_handle, int):
            # Test doubles do not own an OS handle.
            return
        if not self._kernel32.AssignProcessToJobObject(
                self._handle, ctypes.c_void_p(raw_handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def process_ids(self) -> tuple[int, ...]:
        if self._handle is None:
            return ()
        import ctypes
        buffer = ctypes.create_string_buffer(4096)
        returned = ctypes.c_ulong()
        if not self._kernel32.QueryInformationJobObject(
                self._handle, 3, buffer, len(buffer), ctypes.byref(returned)):
            return ()
        count = ctypes.c_ulong.from_buffer(buffer, 4).value
        offset = 8
        values = (ctypes.c_size_t * count).from_buffer(buffer, offset)
        return tuple(int(value) for value in values)

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._kernel32.CloseHandle(handle)


_CONTACT_LABEL = re.compile(r"\bnum[-_ ]?contacts?\b", re.IGNORECASE)
_CONTACT_SCALAR = re.compile(
    r"\bnum[-_ ]?contacts?\b\s*[:=]\s*(\d+)", re.IGNORECASE)
_CONTACT_TUPLE = re.compile(r"[,;]\s*(\d+)\s*[\)\]]")


def _contact_counts(line: str) -> tuple[int, ...]:
    """Extract PPF ``num-contact`` metrics without treating other numbers as contacts."""
    label = _CONTACT_LABEL.search(line)
    if label is None:
        return ()
    tail = line[label.start():]
    scalar = _CONTACT_SCALAR.search(tail)
    if scalar is not None:
        return (int(scalar.group(1)),)
    # PPF metrics may be logged as ``[(simulation_time, count), ...]``.
    return tuple(int(match.group(1)) for match in _CONTACT_TUPLE.finditer(tail))


def format_windows_exit_code(code: int | None) -> str:
    if code is None:
        return "unknown"
    unsigned = int(code) & 0xFFFFFFFF
    signed = unsigned if unsigned < 0x80000000 else unsigned - 0x100000000
    if sys.platform == "win32" or int(code) != signed:
        return f"{signed} (0x{unsigned:08X}; unsigned {unsigned})"
    return str(code)


def _solver_activity(line: str) -> tuple[str, str] | None:
    """Translate known PPF stdout markers into stable, user-facing activity."""
    value = line.strip()
    contacts = _contact_counts(value)
    if contacts:
        return "BUILDING_CONTACTS", f"Assembling contacts · {contacts[-1]:,} contacts"
    match = re.search(r"newton step\s+(\d+)", value, re.IGNORECASE)
    if match:
        return "SOLVING_CONSTRAINTS", f"Newton solve · step {match.group(1)}"
    match = re.match(r"\*\s*iter\s*:\s*(\d+)", value, re.IGNORECASE)
    if match:
        return "SOLVING_CONSTRAINTS", f"Solving linear system · {match.group(1)} iterations"
    markers = (
        ("asm_contact", "BUILDING_CONTACTS", "Assembling contacts"),
        ("matrix_assembly", "SOLVING_CONSTRAINTS", "Assembling system matrix"),
        ("linsolve", "SOLVING_CONSTRAINTS", "Solving linear system"),
        ("line_search", "SOLVING_CONSTRAINTS", "Line search"),
        ("check_intersection", "DETECTING_COLLISIONS", "Checking intersections"),
        ("error reduction step", "SOLVING_CONSTRAINTS", "Reducing solver error"),
    )
    lowered = value.lower()
    for marker, code, label in markers:
        if marker in lowered:
            return code, label
    return None


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
    cleanup_progress_file: bool = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        executable = self.executable_path.expanduser().resolve()
        workdir = self.working_directory.expanduser().resolve()
        generated_progress = self.progress_file is None
        progress = (self.progress_file.expanduser().resolve() if self.progress_file else
                    Path(gettempdir()).resolve() / "cloth-next" / f"progress-{uuid.uuid4().hex}.log")
        object.__setattr__(self, "executable_path", executable)
        object.__setattr__(self, "working_directory", workdir)
        object.__setattr__(self, "progress_file", progress)
        object.__setattr__(self, "cleanup_progress_file", generated_progress)
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
    contact_peak: int = 0
    contact_last: int = 0
    contact_samples: int = 0
    activity_code: str = ""
    activity_message: str = ""
    owned_process_ids: tuple[int, ...] = ()
    launch_id: str = ""
    started_at: float | None = None
    exit_observed_at: float | None = None
    termination_requested: bool = False


class SolverProcessManager:
    def __init__(self, config: SolverProcessConfig) -> None:
        self.config = config
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[tuple[str, str]] = queue.Queue()
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._threads: list[threading.Thread] = []
        self._job: _WindowsJob | None = None
        self._contact_peak = 0
        self._contact_last = 0
        self._contact_samples = 0
        self._activity_code = ""
        self._activity_message = ""
        self._launch_id = ""
        self._started_at: float | None = None
        self._exit_observed_at: float | None = None
        self._exit_code: int | None = None
        self._termination_requested = False
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
        # A launch generation owns all process-derived state.  Never let a
        # previous process' output or activity contaminate its replacement.
        self._launch_id = uuid.uuid4().hex
        self._started_at = time.time()
        self._exit_observed_at = None
        self._exit_code = None
        self._termination_requested = False
        self._stdout.clear()
        self._stderr.clear()
        self._contact_peak = self._contact_last = self._contact_samples = 0
        self._activity_code = self._activity_message = ""
        while not self._lines.empty():
            try:
                self._lines.get_nowait()
            except queue.Empty:
                break
        self.config.progress_file.parent.mkdir(parents=True, exist_ok=True)
        sanitized = [self.config.executable_path.name, "--host", self.config.host,
                     "--port", str(self.config.port), "--progress-file", "<instance-progress-file>"]
        if self.config.debug:
            sanitized.append("--debug")
        log_with_context(self._logger, 20, "process start attempt",
                         {"launch_id": self._launch_id,
                          "executable": str(self.config.executable_path),
                          "working_directory": str(
                              self.config.working_directory),
                          "ownership": self.ownership.name,
                          "host": self.config.host, "port": self.config.port,
                          "arguments": sanitized,
                          "environment_overrides": tuple(
                              key for key, _value in self.config.environment)})
        try:
            job = _WindowsJob()
            self._process = subprocess.Popen(
                self.config.arguments(), cwd=self.config.working_directory,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                bufsize=1, shell=False, env=self.config.subprocess_environment(),
            )
            try:
                job.assign(self._process)
            except BaseException:
                try:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=self.config.shutdown_timeout)
                    except subprocess.TimeoutExpired:
                        job.close()
                        self._process.wait(timeout=self.config.shutdown_timeout)
                finally:
                    job.close()
                    self._process = None
                raise
            self._job = job
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if isinstance(exc, PermissionError) or winerror == 5:
                user_message = "Windows denied access while starting the solver."
                failure_kind = "WINDOWS_ACCESS_DENIED"
            elif isinstance(exc, FileNotFoundError) or winerror in {2, 3}:
                user_message = "The solver executable could not be found."
                failure_kind = "EXECUTABLE_MISSING"
            elif winerror in {126, 127}:
                user_message = (
                    "The solver could not start because a runtime dependency "
                    "is missing.")
                failure_kind = "RUNTIME_DEPENDENCY_MISSING"
            else:
                user_message = "The solver process could not be created."
                failure_kind = "PROCESS_CREATION_FAILED"
            raise ClothNextError(ErrorRecord.create(
                category=ErrorCategory.SOLVER_INSTALLATION,
                user_message=user_message,
                technical_message=(
                    f"failure_kind={failure_kind}; "
                    f"Popen failed: {type(exc).__name__}: {exc}; "
                    f"winerror={winerror}; launch_id={self._launch_id}"),
                recommended_action="Verify the executable, permissions, and solver installation.",
                recoverable=True,
                context={"failure_kind": failure_kind,
                         "launch_id": self._launch_id,
                         "executable": str(self.config.executable_path),
                         "working_directory": str(
                             self.config.working_directory),
                         "winerror": winerror},
                exception=exc,
            )) from exc
        log_with_context(self._logger, 20, "process started",
                         {"launch_id": self._launch_id,
                          "process_id": self._process.pid})
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
            for count in _contact_counts(line):
                self._contact_last = count
                self._contact_peak = max(self._contact_peak, count)
                self._contact_samples += 1
            activity = _solver_activity(line)
            if activity is not None:
                self._activity_code, self._activity_message = activity

    def poll(self) -> ProcessPoll:
        self._drain()
        code = (
            self._exit_code if self._exit_observed_at is not None
            else (None if self._process is None else self._process.poll()))
        if code is not None and self._exit_observed_at is None:
            self._exit_code = code
            self._exit_observed_at = time.time()
        return ProcessPoll(
            running=self._process is not None and code is None,
            process_id=None if self._process is None else self._process.pid,
            exit_code=code, stdout_tail=tuple(self._stdout[-40:]), stderr_tail=tuple(self._stderr[-40:]),
            contact_peak=self._contact_peak,
            contact_last=self._contact_last,
            contact_samples=self._contact_samples,
            activity_code=self._activity_code,
            activity_message=self._activity_message,
            owned_process_ids=(
                self._job.process_ids() if self._job is not None else ()),
            progress=read_progress(self.config.progress_file),
            launch_id=self._launch_id,
            started_at=self._started_at,
            exit_observed_at=self._exit_observed_at,
            termination_requested=self._termination_requested,
        )

    def final_poll(self) -> ProcessPoll:
        """Drain bounded final output after an already-observed process exit."""
        initial = self.poll()
        if initial.running:
            return initial
        deadline = time.monotonic() + min(
            1.0, self.config.shutdown_timeout)
        for thread in tuple(self._threads):
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
        self._drain()
        return self.poll()

    def stop(self) -> ProcessPoll:
        if self.ownership is not ConnectionOwnership.OWNED_PROCESS:
            raise PermissionError("external server must never be stopped by Cloth NeXt")
        process = self._process
        if process is None:
            return self.poll()
        log_with_context(self._logger, 20, "shutdown attempt", {"process_id": process.pid})
        owned_process_ids = (
            self._job.process_ids() if self._job is not None else ())
        if process.poll() is None:
            self._termination_requested = True
            process.terminate()
            try:
                process.wait(timeout=self.config.shutdown_timeout)
            except subprocess.TimeoutExpired:
                if self._job is not None:
                    self._job.close()
                else:
                    process.kill()
                process.wait(timeout=self.config.shutdown_timeout)
        else:
            process.wait()
        # The server may already be gone while its solver child remains alive.
        # Closing the kill-on-close job terminates every still-owned descendant
        # before pipe readers are joined.
        if self._job is not None:
            self._job.close()
        for thread in self._threads:
            thread.join(timeout=self.config.shutdown_timeout)
        if any(thread.is_alive() for thread in self._threads):
            raise RuntimeError(
                "process reader thread did not stop after owned process-tree "
                f"shutdown; owned_process_ids={owned_process_ids}")
        result = self.final_poll()
        log_with_context(self._logger, 20, "shutdown result", {"exit_code": result.exit_code})
        self._process = None
        self._job = None
        self._threads.clear()
        if self.config.cleanup_progress_file:
            # The owned process is reaped and every pipe reader is joined
            # above. Only now can the generated progress file be unlinked.
            outcome = delete_owned(
                self.config.progress_file,
                root=self.config.progress_file.parent,
                ownership_authenticated=True,
                lifecycle_stage="SOLVER_PROCESS_STOP",
                artifact_type="progress_file")
            if not outcome.success:
                log_with_context(
                    self._logger, 30,
                    "Owned solver progress cleanup remains pending", {
                        "diagnostic": outcome.technical_diagnostic()})
        return result

    def restart(self) -> None:
        if self.ownership is not ConnectionOwnership.OWNED_PROCESS:
            raise PermissionError("external server cannot be restarted")
        self.stop()
        self.start()

    def early_exit_error(self, poll: ProcessPoll) -> ClothNextError:
        if not poll.running:
            poll = self.final_poll()
        lifetime = (
            max(0.0, poll.exit_observed_at - poll.started_at)
            if poll.started_at is not None
            and poll.exit_observed_at is not None else None)
        if poll.exit_code == 0:
            user_message = (
                "The solver exited cleanly without a shutdown request.")
            failure_kind = "UNREQUESTED_CLEAN_EXIT"
        elif poll.exit_code is None:
            user_message = (
                "The solver process ended, but no exit code was available.")
            failure_kind = "PROCESS_DISAPPEARED"
        else:
            user_message = (
                f"The solver exited with code "
                f"{format_windows_exit_code(poll.exit_code)}.")
            failure_kind = "NONZERO_PROCESS_EXIT"
        return ClothNextError(ErrorRecord.create(
            category=ErrorCategory.SOLVER_CONNECTION,
            user_message=user_message,
            technical_message=(
                f"failure_kind={failure_kind}; launch_id={poll.launch_id}; "
                f"control_server_pid={poll.process_id}; "
                f"control_server_exit_code={format_windows_exit_code(poll.exit_code)}; "
                f"process_started_at={poll.started_at}; "
                f"process_exit_observed_at={poll.exit_observed_at}; "
                f"process_lifetime_seconds={lifetime}; "
                f"termination_requested={poll.termination_requested}; "
                f"owned_process_ids={poll.owned_process_ids}; "
                f"contacts(last={poll.contact_last}, peak={poll.contact_peak}, "
                f"samples={poll.contact_samples}); stdout_tail={poll.stdout_tail}; "
                f"stderr_tail={poll.stderr_tail}; progress_tail={poll.progress.tail}"),
            recommended_action=(
                "Inspect the retained run log. Cloth NeXt terminated every "
                "remaining process in the owned solver job before allowing a retry."),
            recoverable=True,
            context={"control_server_pid": poll.process_id,
                     "exit_code": poll.exit_code,
                     "owned_process_ids": poll.owned_process_ids,
                     "failure_kind": failure_kind,
                     "launch_id": poll.launch_id,
                     "started_at": poll.started_at,
                     "exit_observed_at": poll.exit_observed_at,
                     "lifetime_seconds": lifetime,
                     "termination_requested": poll.termination_requested},
        ))
