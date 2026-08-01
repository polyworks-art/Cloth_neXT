import io
from pathlib import Path
import subprocess

from cloth_next.newton_preview.client import NewtonWorkerClient


class _Process:
    def __init__(self, waits):
        self.pid = 42
        self.stdin = _Stream(); self.stdout = _Stream(); self.stderr = _Stream()
        self.waits = list(waits)
        self.returncode = None
        self.terminated = False; self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        result = self.waits.pop(0) if self.waits else 0
        if result == "timeout":
            raise subprocess.TimeoutExpired("worker", timeout)
        self.returncode = result
        return result

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class _Stream(io.BytesIO):
    def close(self):
        pass


def _client(process):
    client = NewtonWorkerClient(Path("python"), package_root=Path.cwd())
    client.process = process
    return client


def test_worker_shutdown_is_cooperative_first():
    process = _Process([0])
    client = _client(process)
    client.shutdown(grace=0.1)
    assert b'"command":"shutdown"' in process.stdin.getvalue()
    assert process.terminated is False
    assert process.killed is False
    assert client.process is None


def test_worker_shutdown_terminates_then_kills_only_owned_process():
    process = _Process(["timeout", "timeout", 0])
    client = _client(process)
    client.shutdown(grace=0.01)
    assert process.terminated is True
    assert process.killed is True
    assert client.process is None
