# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import subprocess
import sys
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cloth_next.core.errors import ClothNextError
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.process import (
    SolverProcessConfig, SolverProcessManager, _contact_counts,
    _WindowsJob, _solver_activity, format_windows_exit_code)


def config(tmp_path, ownership=ConnectionOwnership.OWNED_PROCESS):
    return SolverProcessConfig(Path(sys.executable), tmp_path, ownership_mode=ownership)


def append_log(manager, label, data):
    path = (manager.config.stdout_log_file if label == "stdout"
            else manager.config.stderr_log_file)
    with open(path, "ab") as stream:
        stream.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def test_config_normalizes_paths_and_builds_argument_list(tmp_path):
    cfg = config(tmp_path)
    assert cfg.executable_path.is_absolute()
    assert cfg.progress_file.is_absolute()
    assert cfg.cleanup_progress_file is True
    assert cfg.arguments()[1:7] == ["--host", "127.0.0.1", "--port", "9090", "--progress-file", str(cfg.progress_file)]


def test_caller_owned_progress_file_is_preserved(tmp_path):
    progress = tmp_path / "diagnostics" / "progress.log"
    cfg = SolverProcessConfig(Path(sys.executable), tmp_path, progress_file=progress)
    assert cfg.cleanup_progress_file is False
    assert cfg.stdout_log_file.parent != progress.parent
    assert cfg.stderr_log_file.parent != progress.parent


def test_generated_progress_file_is_removed_after_owned_process_stops(tmp_path):
    process = MagicMock()
    process.poll.side_effect = [None, 0]
    process.pid = 123
    process.stdout = StringIO("")
    process.stderr = StringIO("")
    manager = SolverProcessManager(config(tmp_path))
    manager.config.progress_file.parent.mkdir(parents=True, exist_ok=True)
    manager.config.progress_file.write_text("ready", encoding="utf-8")
    with patch("cloth_next.ppf.process.subprocess.Popen", return_value=process):
        manager.start()
        manager.stop()
    assert not manager.config.progress_file.exists()


def test_missing_or_directory_executable_rejected(tmp_path):
    with pytest.raises(ValueError):
        SolverProcessConfig(tmp_path / "missing.exe", tmp_path)
    with pytest.raises(ValueError):
        SolverProcessConfig(tmp_path, tmp_path)


def test_popen_uses_argument_list_and_shell_false(tmp_path):
    process = MagicMock()
    process.poll.return_value = None
    process.stdout = StringIO("")
    process.stderr = StringIO("")
    with patch("cloth_next.ppf.process.subprocess.Popen", return_value=process) as popen:
        manager = SolverProcessManager(config(tmp_path))
        manager.start()
        args, kwargs = popen.call_args
        assert isinstance(args[0], list)
        assert kwargs["shell"] is False
        process.poll.return_value = 0
        manager.stop()


def test_windows_access_denied_is_not_collapsed_into_generic_launch_failure(
        tmp_path):
    denied = PermissionError(13, "Access is denied")
    denied.winerror = 5
    with patch("cloth_next.ppf.process.subprocess.Popen",
               side_effect=denied):
        manager = SolverProcessManager(config(tmp_path))
        with pytest.raises(ClothNextError) as caught:
            manager.start()

    assert caught.value.record.user_message == (
        "Windows denied access while starting the solver.")
    assert ("WINDOWS_ACCESS_DENIED" in
            caught.value.record.technical_message)
    assert not manager.config.stdout_log_file.exists()
    assert not manager.config.stderr_log_file.exists()


@pytest.mark.parametrize(("error", "message", "failure_kind"), [
    (FileNotFoundError(2, "not found"),
     "The solver executable could not be found.", "EXECUTABLE_MISSING"),
    (OSError(126, "module not found"),
     "The solver could not start because a runtime dependency is missing.",
     "RUNTIME_DEPENDENCY_MISSING"),
    (OSError(8, "not enough memory"),
     "The solver process could not be created.", "PROCESS_CREATION_FAILED"),
])
def test_process_creation_failures_keep_precise_classification(
        tmp_path, error, message, failure_kind):
    error.winerror = error.errno
    with patch("cloth_next.ppf.process.subprocess.Popen",
               side_effect=error):
        manager = SolverProcessManager(config(tmp_path))
        with pytest.raises(ClothNextError) as caught:
            manager.start()

    assert caught.value.record.user_message == message
    assert failure_kind in caught.value.record.technical_message
    assert caught.value.record.original_exception_type == type(error).__name__


@pytest.mark.parametrize(("exit_code", "message", "failure_kind"), [
    (0, "The solver exited cleanly without a shutdown request.",
     "UNREQUESTED_CLEAN_EXIT"),
    (9, "The solver exited with code 9",
     "NONZERO_PROCESS_EXIT"),
])
def test_unrequested_exit_classification_preserves_lifetime(
        tmp_path, exit_code, message, failure_kind):
    process = MagicMock()
    process.poll.return_value = exit_code
    process.pid = 321
    process.stdout = StringIO("")
    process.stderr = StringIO("")
    manager = SolverProcessManager(config(tmp_path))
    manager._process = process
    manager._launch_id = "launch-exit"
    manager._started_at = 100.0
    manager._exit_observed_at = 103.5
    manager._exit_code = exit_code

    error = manager.early_exit_error(manager.poll())
    manager.stop()

    assert error.record.user_message.startswith(message)
    assert failure_kind in error.record.technical_message
    assert "process_lifetime_seconds=3.5" in error.record.technical_message
    assert "termination_requested=False" in error.record.technical_message


def test_disappeared_process_without_exit_code_is_not_generic(tmp_path):
    manager = SolverProcessManager(config(tmp_path))
    manager._launch_id = "launch-disappeared"

    error = manager.early_exit_error(manager.poll())

    assert error.record.user_message == (
        "The solver process ended, but no exit code was available.")
    assert "PROCESS_DISAPPEARED" in error.record.technical_message


def test_new_launch_generation_clears_stale_output_and_activity(tmp_path):
    processes = []
    for pid in (101, 202):
        process = MagicMock()
        process.poll.return_value = None
        process.pid = pid
        process.stdout = StringIO("")
        process.stderr = StringIO("")
        processes.append(process)
    manager = SolverProcessManager(config(tmp_path))
    with patch("cloth_next.ppf.process.subprocess.Popen",
               side_effect=processes):
        manager.start()
        first_launch = manager.poll().launch_id
        append_log(manager, "stderr", "old crash\n")
        append_log(manager, "stdout", "* iter: 99\n")
        manager.poll()
        processes[0].poll.return_value = 0
        manager.stop()
        manager.start()
        second = manager.poll()
        processes[1].poll.return_value = 0
        manager.stop()

    assert second.launch_id and second.launch_id != first_launch
    assert second.stdout_tail == second.stderr_tail == ()
    assert not second.activity_message


def test_early_exit_drains_final_stderr_before_classification(tmp_path):
    process = MagicMock()
    process.poll.return_value = 7
    process.pid = 123
    process.stdout = StringIO("")
    process.stderr = StringIO("")
    manager = SolverProcessManager(config(tmp_path))
    manager._process = process
    manager._launch_id = "launch-final-output"
    append_log(manager, "stderr", "CUDA initialization failed\n")

    error = manager.early_exit_error(manager.poll())

    assert "CUDA initialization failed" in error.record.technical_message
    assert "launch-final-output" in error.record.technical_message
    manager.stop()


def test_external_server_cannot_start_stop_or_restart(tmp_path):
    manager = SolverProcessManager(config(tmp_path, ConnectionOwnership.EXTERNAL_SERVER))
    with pytest.raises(PermissionError): manager.start()
    with pytest.raises(PermissionError): manager.stop()
    with pytest.raises(PermissionError): manager.restart()


def test_owned_process_is_terminated_waited_and_reaped(tmp_path):
    process = MagicMock()
    process.poll.side_effect = [None, 0]
    process.pid = 123
    process.stdout = StringIO("")
    process.stderr = StringIO("")
    with patch("cloth_next.ppf.process.subprocess.Popen", return_value=process):
        manager = SolverProcessManager(config(tmp_path))
        manager.start()
        manager.stop()
    process.terminate.assert_called_once()
    process.wait.assert_called()


def test_contact_metric_parser_supports_scalar_and_ppf_series():
    assert _contact_counts("num-contact: 123456") == (123456,)
    assert _contact_counts("num_contact=[(0.0, 12), (0.1, 34)]") == (12, 34)
    assert _contact_counts("frame=9 vertices=12000") == ()


def test_process_poll_aggregates_contact_peak(tmp_path):
    manager = SolverProcessManager(config(tmp_path))
    append_log(manager, "stdout", "num-contact: 120\n")
    append_log(manager, "stderr", "num-contact: 85\n")
    poll = manager.poll()
    manager.stop()
    assert (poll.contact_last, poll.contact_peak, poll.contact_samples) == (85, 120, 2)


@pytest.mark.parametrize(("line", "expected"), (
    ("> asm_contact...17 msec", ("BUILDING_CONTACTS", "Assembling contacts")),
    ("* num_contact: 9997", ("BUILDING_CONTACTS", "Assembling contacts · 9,997 contacts")),
    ("------ newton step 4 ------", ("SOLVING_CONSTRAINTS", "Newton solve · step 4")),
    ("* iter: 40", ("SOLVING_CONSTRAINTS", "Solving linear system · 40 iterations")),
    ("> check_intersection...4 msec", ("DETECTING_COLLISIONS", "Checking intersections")),
    ("* max_dx: 1.0e-2", None),
))
def test_solver_activity_parser_is_curated(line, expected):
    assert _solver_activity(line) == expected


def test_windows_exit_code_format_includes_signed_hex_and_unsigned():
    assert format_windows_exit_code(4294967295) == \
        "-1 (0xFFFFFFFF; unsigned 4294967295)"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object only")
def test_windows_job_close_terminates_spawned_descendant():
    import ctypes
    from ctypes import wintypes

    script = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "print(p.pid,flush=True);time.sleep(60)")
    parent = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    job = _WindowsJob()
    try:
        job.assign(parent)
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline().strip())
        parent.stdout.close()
        assert parent.pid in job.process_ids()
        assert child_pid in job.process_ids()
        job.close()
        parent.wait(timeout=5)

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            handle = kernel32.OpenProcess(0x1000, False, child_pid)
            if not handle:
                break
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            if code.value != 259:
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"owned descendant {child_pid} remained alive")
    finally:
        job.close()
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)


def test_process_poll_exposes_latest_curated_activity(tmp_path):
    manager = SolverProcessManager(config(tmp_path))
    append_log(manager, "stdout", "> linsolve...6 msec\n")
    append_log(manager, "stdout", "* iter: 44\n")
    poll = manager.poll()
    manager.stop()
    assert poll.activity_code == "SOLVING_CONSTRAINTS"
    assert poll.activity_message == "Solving linear system · 44 iterations"


def test_file_tail_replaces_malformed_output_and_bounds_large_burst(tmp_path):
    manager = SolverProcessManager(config(tmp_path))
    burst = b"noise\n" * 20_000 + b"bad-utf8:\xff\nnum-contact: 321\n"
    append_log(manager, "stdout", burst)

    poll = manager.poll()

    assert len(poll.stdout_tail) <= 40
    assert "bad-utf8:\ufffd" in poll.stdout_tail
    assert poll.contact_last == 321
    assert manager._log_pending["stdout"] == b""
    manager.stop()


def test_owned_launch_uses_real_files_and_no_reader_threads(tmp_path):
    process = MagicMock()
    process.poll.side_effect = [None, 0]
    process.pid = 123
    with patch("cloth_next.ppf.process.subprocess.Popen",
               return_value=process) as popen:
        manager = SolverProcessManager(config(tmp_path))
        manager.start()
        stdout_target = popen.call_args.kwargs["stdout"]
        stderr_target = popen.call_args.kwargs["stderr"]
        assert stdout_target is not subprocess.PIPE
        assert stderr_target is not subprocess.PIPE
        assert stdout_target.closed and stderr_target.closed
        assert not any(thread.name.startswith("cloth-next-ppf-")
                       for thread in __import__("threading").enumerate())
        manager.stop()

    assert not manager.config.stdout_log_file.exists()
    assert not manager.config.stderr_log_file.exists()
