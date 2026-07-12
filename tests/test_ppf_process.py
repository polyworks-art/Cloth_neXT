# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager


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
