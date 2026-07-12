# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import socket
import threading
import time

import pytest

from cloth_next.core.errors import ClothNextError
from cloth_next.ppf.transport import TransportConfig, query_status, status_request_bytes


class TcpTestDouble:
    """Deterministic transport double; it is not evidence of PPF compatibility."""
    def __init__(self, parts, delay=0.0):
        self.parts = parts
        self.delay = delay
        self.received = b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.sock.listen(1)
        self.thread = threading.Thread(target=self._serve)
        self.thread.start()

    def _serve(self):
        connection, _ = self.sock.accept()
        with connection:
            self.received = connection.recv(4096)
            for part in self.parts:
                if self.delay:
                    time.sleep(self.delay)
                connection.sendall(part)
        self.sock.close()

    def close(self):
        self.thread.join(timeout=2)
        assert not self.thread.is_alive()


def test_exact_status_request_bytes():
    payload = b"--name demo"
    assert status_request_bytes("demo") == b"TCMD" + len(payload).to_bytes(4, "big") + payload


def test_partial_reads_are_reassembled():
    server = TcpTestDouble([b'{"protocol_', b'version":"0.11",', b'"status":"NO_DATA"}\n'])
    result = query_status("127.0.0.1", server.port, "demo", TransportConfig())
    server.close()
    assert result["protocol_version"] == "0.11"
    assert server.received == status_request_bytes("demo")


@pytest.mark.parametrize("parts,technical", [
    ([b"\xff"], "UTF-8"),
    ([b"not json"], "JSON"),
])
def test_invalid_responses(parts, technical):
    server = TcpTestDouble(parts)
    with pytest.raises(ClothNextError) as caught:
        query_status("127.0.0.1", server.port, "demo", TransportConfig())
    server.close()
    assert technical in caught.value.record.technical_message


def test_oversize_response_is_rejected():
    server = TcpTestDouble([b"x" * 65])
    with pytest.raises(ClothNextError):
        query_status("127.0.0.1", server.port, "demo", TransportConfig(max_response_bytes=64))
    server.close()


def test_read_timeout_is_categorized():
    server = TcpTestDouble([b"{}"], delay=0.1)
    with pytest.raises(ClothNextError) as caught:
        query_status("127.0.0.1", server.port, "demo", TransportConfig(read_timeout=0.01))
    server.close()
    assert "timeout" in caught.value.record.technical_message.lower()

