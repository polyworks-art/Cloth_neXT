# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import subprocess
import sys
import time
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.process import (
    SolverProcessConfig, SolverProcessManager, _contact_counts,
    _WindowsJob, _solver_activity, format_windows_exit_code)


def config(tmp_path, ownership=ConnectionOwnership.OWNED_PROCESS):
    return SolverProcessConfig(Path(sys.executable), tmp_path, ownership_mode=ownership)


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
    manager._lines.put(("stdout", "num-contact: 120"))
    manager._lines.put(("stderr", "num-contact: 85"))
    poll = manager.poll()
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
    manager._lines.put(("stdout", "> linsolve...6 msec"))
    manager._lines.put(("stdout", "* iter: 44"))
    poll = manager.poll()
    assert poll.activity_code == "SOLVING_CONSTRAINTS"
    assert poll.activity_message == "Solving linear system · 44 iterations"
