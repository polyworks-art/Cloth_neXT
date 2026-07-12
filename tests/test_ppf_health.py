import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cloth_next.core.state import ApplicationState
from cloth_next.core.errors import ClothNextError
from cloth_next.core.errors import ErrorCategory, ErrorRecord
from cloth_next.ppf.health import HealthSnapshot, query_health, start_owned_and_wait
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.process import ProcessPoll
from cloth_next.ppf.progress import ProgressSnapshot
from cloth_next.ppf.transport import TransportConfig
from tests.test_ppf_transport import TcpTestDouble


def response(protocol="0.11", status="READY"):
    return json.dumps({"protocol_version": protocol, "status": status, "error": "", "frame": 0}).encode() + b"\n"


def test_owned_health_snapshot_is_fully_verified_and_immutable():
    server = TcpTestDouble([response()])
    health = query_health(host="127.0.0.1", port=server.port, project_name="demo",
        ownership=ConnectionOwnership.OWNED_PROCESS, transport=TransportConfig(),
        local_versions=("0.1.0", "0.11", "1"), process_running=True, process_id=42)
    server.close()
    assert health.reachable and health.compatible
    assert health.application_state is ApplicationState.READY
    assert health.schema_version == "1"
    with pytest.raises(FrozenInstanceError):
        health.reachable = False


def test_external_health_does_not_claim_schema_compatibility():
    server = TcpTestDouble([response()])
    health = query_health(host="127.0.0.1", port=server.port, project_name="demo",
        ownership=ConnectionOwnership.EXTERNAL_SERVER, transport=TransportConfig())
    server.close()
    assert health.reachable
    assert not health.compatible
    assert health.schema_version is None
    assert health.last_error is not None


def test_non_ppf_service_is_port_conflict():
    server = TcpTestDouble([b"hello"])
    health = query_health(host="127.0.0.1", port=server.port, project_name="demo",
        ownership=ConnectionOwnership.EXTERNAL_SERVER, transport=TransportConfig())
    server.close()
    assert not health.reachable
    assert not health.compatible
    assert health.last_error is not None


def test_protocol_mismatch_is_rejected():
    server = TcpTestDouble([response("0.10")])
    health = query_health(host="127.0.0.1", port=server.port, project_name="demo",
        ownership=ConnectionOwnership.OWNED_PROCESS, transport=TransportConfig(),
        local_versions=("0.1.0", "0.10", "1"))
    server.close()
    assert health.reachable
    assert not health.compatible


class FakeManager:
    def __init__(self, polls):
        self.config = SimpleNamespace(host="127.0.0.1", port=19091, connect_timeout=.01,
            read_timeout=.01, startup_timeout=.03)
        self.polls = iter(polls)
        self.started = False
        self.stopped = False
    def executable_version(self): return ("0.1.0", "0.11", "1")
    def start(self): self.started = True
    def poll(self): return next(self.polls)
    def stop(self): self.stopped = True
    def early_exit_error(self, poll):
        return ClothNextError(ErrorRecord.create(category=ErrorCategory.SOLVER_INSTALLATION,
            user_message="early exit", technical_message=f"exit={poll.exit_code}",
            recommended_action="inspect logs"))


def poll(running=True, ready=False, exit_code=None):
    return ProcessPoll(running, 42, exit_code, (), (), ProgressSnapshot(True, ready, ()))


def test_start_waits_for_delayed_ready_marker_and_query():
    manager = FakeManager([poll(ready=False), poll(ready=True)])
    ready = HealthSnapshot(True, True, ConnectionOwnership.OWNED_PROCESS, True,
        "127.0.0.1", 19091, "0.1.0", "0.11", "1", "NO_DATA", None, 42, None, None,
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    with patch("cloth_next.ppf.health.port_reachable", return_value=False), \
         patch("cloth_next.ppf.health.query_health", return_value=ready), \
         patch("cloth_next.ppf.health.time.sleep"):
        assert start_owned_and_wait(manager) is ready
    assert manager.started and not manager.stopped


def test_startup_timeout_stops_and_reaps_owned_process():
    manager = FakeManager([poll()] * 100)
    with patch("cloth_next.ppf.health.port_reachable", return_value=False), \
         patch("cloth_next.ppf.health.time.monotonic", side_effect=[0.0, 1.0]), \
         patch("cloth_next.ppf.health.time.sleep"):
        with pytest.raises(ClothNextError):
            start_owned_and_wait(manager)
    assert manager.stopped


def test_early_process_exit_is_reported_and_stopped():
    manager = FakeManager([poll(running=False, exit_code=7)])
    with patch("cloth_next.ppf.health.port_reachable", return_value=False):
        with pytest.raises(ClothNextError) as caught:
            start_owned_and_wait(manager)
    assert "exit=7" in caught.value.record.technical_message
    assert manager.stopped
