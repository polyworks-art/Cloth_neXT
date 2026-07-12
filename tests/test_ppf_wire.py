# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Typed wire operations against an in-process fake TCP endpoint: exact
request bytes, framing, and every rejection path."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from cloth_next.core.errors import ClothNextError
from cloth_next.ppf import wire
from cloth_next.ppf.transport import TransportConfig

FIXTURES = Path(__file__).parent / "fixtures" / "ppf_0_11"
CONFIG = TransportConfig(connect_timeout=2.0, read_timeout=2.0)


class FakeServer:
    """One-connection-at-a-time scripted TCP endpoint."""

    def __init__(self, handler):
        self._handler = handler
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self.address = wire.ServerAddress("127.0.0.1",
                                          self._listener.getsockname()[1])
        self.received: list[bytes] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                connection, _peer = self._listener.accept()
            except OSError:
                return
            with connection:
                try:
                    self._handler(connection, self.received)
                except OSError:
                    pass

    def close(self):
        self._listener.close()


@pytest.fixture
def make_server():
    servers = []

    def factory(handler):
        server = FakeServer(handler)
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.close()


def _read_exact(connection, count):
    data = b""
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            return data
        data += chunk
    return data


def _read_tcmd(connection):
    header = _read_exact(connection, 4)
    assert header == b"TCMD"
    length = int.from_bytes(_read_exact(connection, 4), "big")
    return _read_exact(connection, length)


# --- exact request bytes -----------------------------------------------------

def test_tcmd_request_bytes_match_goldens():
    assert wire.tcmd_request_bytes("clothnext_abc123", "build") == \
        (FIXTURES / "tcmd_build_request.bin").read_bytes()
    assert wire.tcmd_request_bytes("clothnext_abc123", "terminate") == \
        (FIXTURES / "tcmd_terminate_request.bin").read_bytes()
    status = wire.tcmd_request_bytes("p1")
    assert status == b"TCMD" + (9).to_bytes(4, "big") + b"--name p1"


def test_tcmd_rejects_unknown_request_and_bad_names():
    with pytest.raises(ValueError):
        wire.tcmd_request_bytes("p1", "resume")  # not used in Phase 3A
    with pytest.raises(ValueError):
        wire.tcmd_request_bytes("has space", "build")
    with pytest.raises(ValueError):
        wire.tcmd_request_bytes("", None)


# --- TCMD roundtrip ----------------------------------------------------------

def test_send_tcmd_parses_json_response(make_server):
    def handler(connection, received):
        received.append(_read_tcmd(connection))
        connection.sendall(b'{"status": "READY", "frame": 3}\n')

    server = make_server(handler)
    response = wire.send_tcmd(server.address, CONFIG, "proj", wire.REQUEST_BUILD)
    assert response == {"status": "READY", "frame": 3}
    assert server.received[0] == b"--name proj --request build"


def test_send_tcmd_rejects_server_error(make_server):
    server = make_server(lambda c, r: c.sendall(b'{"error": "boom"}\n'))
    with pytest.raises(ClothNextError,
                       match="server error during|recv failed"):
        wire.send_tcmd(server.address, CONFIG, "proj", wire.REQUEST_START)


@pytest.mark.parametrize("payload", [b"not json\n", b'["array"]\n', b"\xff\xfe\n"])
def test_send_tcmd_rejects_malformed_responses(make_server, payload):
    server = make_server(lambda c, r, p=payload: c.sendall(p))
    with pytest.raises(ClothNextError):
        wire.send_tcmd(server.address, CONFIG, "proj")


def test_send_tcmd_rejects_truncated_response(make_server):
    def handler(connection, _received):
        _read_tcmd(connection)
        connection.sendall(b'{"status": "READY"')  # no newline, then close

    server = make_server(handler)
    with pytest.raises(ClothNextError, match="closed"):
        wire.send_tcmd(server.address, CONFIG, "proj")


def test_send_tcmd_rejects_oversized_response(make_server):
    big = b"x" * (CONFIG.max_response_bytes + 10)

    def handler(connection, _received):
        _read_tcmd(connection)
        connection.sendall(big)

    server = make_server(handler)
    with pytest.raises(ClothNextError, match="exceeded"):
        wire.send_tcmd(server.address, CONFIG, "proj")


# --- upload_atomic -----------------------------------------------------------

def test_upload_atomic_frames_header_and_payloads(make_server):
    data_payload, param_payload = b"D" * 40000, b"P" * 123

    def handler(connection, received):
        assert _read_exact(connection, 4) == b"JSON"
        line = b""
        while not line.endswith(b"\n"):
            line += connection.recv(1)
        header = json.loads(line)
        received.append(header)
        body = _read_exact(connection,
                           header["data_size"] + header["param_size"])
        received.append(body)
        connection.sendall(b"OK\n")

    server = make_server(handler)
    wire.upload_atomic(server.address, CONFIG, project_name="proj",
                       data_payload=data_payload, param_payload=param_payload,
                       data_hash="dh", param_hash="ph")
    header = server.received[0]
    assert header == {"request": "upload_atomic", "name": "proj", "path": "",
                      "data_size": 40000, "param_size": 123,
                      "data_hash": "dh", "param_hash": "ph"}
    assert server.received[1] == data_payload + param_payload


def test_upload_atomic_surfaces_server_error(make_server):
    def handler(connection, _received):
        connection.sendall(b'{"error": "Cannot upload while a build is in '
                           b'progress."}\n')

    server = make_server(handler)
    with pytest.raises(ClothNextError,
                       match="server error during|sendall failed|recv failed"):
        wire.upload_atomic(server.address, CONFIG, project_name="proj",
                           data_payload=b"d", param_payload=b"p",
                           data_hash="x", param_hash="y")


def test_upload_atomic_requires_both_payloads():
    address = wire.ServerAddress("127.0.0.1", 9)
    with pytest.raises(ValueError):
        wire.upload_atomic(address, CONFIG, project_name="proj",
                           data_payload=b"", param_payload=b"p",
                           data_hash="", param_hash="y")


# --- data_receive ------------------------------------------------------------

def test_data_receive_streams_exact_bytes(make_server):
    body = bytes(range(256)) * 200

    def handler(connection, received):
        assert _read_exact(connection, 4) == b"JSON"
        line = b""
        while not line.endswith(b"\n"):
            line += connection.recv(1)
        received.append(json.loads(line))
        connection.sendall(json.dumps({"size": len(body)}).encode() + b"\n")
        connection.sendall(body)

    server = make_server(handler)
    result = wire.data_receive(server.address, CONFIG, project_name="proj",
                               path="session/output/vert_3.bin")
    assert result == body
    assert server.received[0] == {"request": "data_receive", "name": "proj",
                                  "path": "session/output/vert_3.bin"}


def test_data_receive_rejects_truncated_transfer(make_server):
    def handler(connection, _received):
        connection.recv(4096)
        connection.sendall(b'{"size": 1000}\n')
        connection.sendall(b"only-a-little")

    server = make_server(handler)
    with pytest.raises(ClothNextError, match="truncated|closed"):
        wire.data_receive(server.address, CONFIG, project_name="proj",
                          path="session/map.pickle")


def test_data_receive_rejects_oversized_declaration(make_server):
    def handler(connection, _received):
        connection.recv(4096)
        connection.sendall(b'{"size": 999999999999}\n')

    server = make_server(handler)
    with pytest.raises(ClothNextError, match="exceeds bound"):
        wire.data_receive(server.address, CONFIG, project_name="proj",
                          path="session/map.pickle")


def test_data_receive_rejects_invalid_size_and_errors(make_server):
    server = make_server(lambda c, r: (c.recv(4096),
                                       c.sendall(b'{"size": "NaN"}\n')))
    with pytest.raises(ClothNextError, match="no valid size"):
        wire.data_receive(server.address, CONFIG, project_name="proj",
                          path="session/map.pickle")
    server2 = make_server(lambda c, r: (c.recv(4096),
                                        c.sendall(b'{"error": "File not found"}\n')))
    with pytest.raises(ClothNextError, match="server error during"):
        wire.data_receive(server2.address, CONFIG, project_name="proj",
                          path="session/map.pickle")


@pytest.mark.parametrize("path", ["", "/abs/path", "..\\escape",
                                  "a/../escape"])
def test_data_receive_rejects_unsafe_paths(path):
    address = wire.ServerAddress("127.0.0.1", 9)
    with pytest.raises(ValueError):
        wire.data_receive(address, CONFIG, project_name="proj", path=path)
