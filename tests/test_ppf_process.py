# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.process import (
    SolverProcessConfig, SolverProcessManager, _contact_counts,
    _solver_activity)


def config(tmp_path, ownership=ConnectionOwnership.OWNED_PROCESS):
    return SolverProcessConfig(Path(sys.executable), tmp_path, ownership_mode=ownership)


def test_config_normalizes_paths_and_builds_argument_list(tmp_path):
    cfg = config(tmp_path)
    assert cfg.executable_path.is_absolute()
    assert cfg.progress_file.is_absolute()
    assert cfg.arguments()[1:7] == ["--host", "127.0.0.1", "--port", "9090", "--progress-file", str(cfg.progress_file)]


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


def test_process_poll_exposes_latest_curated_activity(tmp_path):
    manager = SolverProcessManager(config(tmp_path))
    manager._lines.put(("stdout", "> linsolve...6 msec"))
    manager._lines.put(("stdout", "* iter: 44"))
    poll = manager.poll()
    assert poll.activity_code == "SOLVING_CONSTRAINTS"
    assert poll.activity_message == "Solving linear system · 44 iterations"
