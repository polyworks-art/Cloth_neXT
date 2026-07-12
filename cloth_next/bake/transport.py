# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small authenticated status schema and test/demo transports."""

from __future__ import annotations

from dataclasses import dataclass
import json
import secrets
from queue import Empty, Queue
import socket
import threading

from .status import BakeSnapshot

MAX_MESSAGE_BYTES = 64 * 1024


MESSAGE_TYPES = {"session_hello", "bake_status", "shutdown", "heartbeat",
                 "ready", "cancel_request", "close_notice"}
_ALIASES = {"status": "bake_status", "cancel": "cancel_request", "close": "close_notice"}

def encode_message(kind: str, token: str, snapshot: BakeSnapshot | None = None) -> bytes:
    kind = _ALIASES.get(kind, kind)
    if kind not in MESSAGE_TYPES:
        raise ValueError("unsupported message kind")
    data = {"type": kind, "token": token}
    if snapshot is not None:
        data["snapshot"] = snapshot.to_dict()
    value = json.dumps(data, separators=(",", ":")).encode("utf-8")
    if len(value) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    return value


def decode_message(value: bytes, token: str) -> dict:
    if len(value) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    try:
        data = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed JSON") from exc
    if not isinstance(data, dict) or data.get("type") not in MESSAGE_TYPES:
        raise ValueError("invalid message schema")
    if not secrets.compare_digest(str(data.get("token", "")), token):
        raise PermissionError("invalid session token")
    if data["type"] == "bake_status":
        if not isinstance(data.get("snapshot"), dict):
            raise ValueError("status snapshot missing")
        data["snapshot"] = BakeSnapshot.from_dict(data["snapshot"])
    return data


def validate_localhost(host: str) -> None:
    if host != "127.0.0.1":
        raise ValueError("transport must bind to 127.0.0.1")


@dataclass
class InMemoryTransport:
    token: str = ""

    def __post_init__(self):
        self.token = self.token or secrets.token_urlsafe(32)
        self.status: Queue[BakeSnapshot] = Queue()
        self.cancel_requests: Queue[bool] = Queue()
        self.closed = False

    def publish(self, snapshot: BakeSnapshot) -> None:
        if not self.closed:
            self.status.put(snapshot)

    def receive(self, timeout: float = 0.0) -> BakeSnapshot | None:
        try:
            return self.status.get(timeout=timeout)
        except Empty:
            return None

    def request_cancel(self) -> None:
        if not self.closed:
            self.cancel_requests.put(True)

    def close(self) -> None:
        self.closed = True


class DemoTransport(InMemoryTransport):
    """Explicit source-app preview transport; never starts work itself."""


class LocalSocketServer:
    """Single-companion authenticated localhost publisher/request queue."""
    def __init__(self, host: str = "127.0.0.1", *, token: str | None = None):
        validate_localhost(host)
        self.token = token or secrets.token_urlsafe(32)
        self.requests: Queue[str] = Queue(maxsize=32)
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, 0)); self._server.listen(1); self._server.settimeout(0.2)
        self.host, self.port = self._server.getsockname()
        self._client = None; self._lock = threading.Lock(); self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ClothNeXtCompanionIPC", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try: client, address = self._server.accept()
            except socket.timeout: continue
            except OSError: break
            if address[0] != "127.0.0.1": client.close(); continue
            client.settimeout(0.2)
            with self._lock:
                if self._client is not None: client.close(); continue
                self._client = client
            try:
                self._send("session_hello")
                buffer = b""
                while not self._stop.is_set():
                    try: chunk = client.recv(4096)
                    except socket.timeout: continue
                    if not chunk: break
                    buffer += chunk
                    if len(buffer) > MAX_MESSAGE_BYTES: break
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        try: message = decode_message(raw, self.token)
                        except (ValueError, PermissionError): continue
                        if message["type"] in {"ready", "cancel_request", "close_notice"}:
                            try: self.requests.put_nowait(message["type"])
                            except Exception: pass
            finally:
                with self._lock:
                    if self._client is client: self._client = None
                client.close()

    def _send(self, kind, snapshot=None):
        data = encode_message(kind, self.token, snapshot) + b"\n"
        with self._lock:
            client = self._client
        if client:
            try: client.sendall(data)
            except OSError: pass

    def publish(self, snapshot): self._send("bake_status", snapshot)
    def shutdown_companion(self): self._send("shutdown")
    def poll_request(self):
        try: return self.requests.get_nowait()
        except Empty: return None
    def close(self):
        self._stop.set(); self.shutdown_companion()
        try: self._server.close()
        except OSError: pass
        with self._lock: client=self._client; self._client=None
        if client:
            try: client.close()
            except OSError: pass
        self._thread.join(timeout=1)


class LocalSocketClient:
    def __init__(self, port: int, token: str):
        self.token=token; self._socket=socket.create_connection(("127.0.0.1", int(port)), timeout=2)
        self._socket.settimeout(0.2); self._buffer=b""; self.closed=False
    def send(self, kind): self._socket.sendall(encode_message(kind, self.token)+b"\n")
    def receive(self, timeout=0.2):
        self._socket.settimeout(timeout)
        while b"\n" not in self._buffer:
            try: chunk=self._socket.recv(4096)
            except socket.timeout: return None
            if not chunk: self.closed=True; return None
            self._buffer += chunk
            if len(self._buffer)>MAX_MESSAGE_BYTES: raise ValueError("message too large")
        raw,self._buffer=self._buffer.split(b"\n",1)
        return decode_message(raw,self.token)
    def request_cancel(self): self.send("cancel_request")
    def close(self):
        if self.closed: return
        try: self.send("close_notice")
        except OSError: pass
        self.closed=True; self._socket.close()
