# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small authenticated status schema and test/demo transports."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import secrets
from queue import Empty, Full, Queue
import socket
import threading

from .status import BakeSnapshot

MAX_MESSAGE_BYTES = 64 * 1024


MESSAGE_TYPES = {"session_hello", "bake_status", "shutdown", "heartbeat",
                 "ready", "cancel_request", "close_notice",
                 "enter_bake_mode", "bake_window_ready", "startup_error"}
_ALIASES = {"status": "bake_status", "cancel": "cancel_request", "close": "close_notice"}

@dataclass(frozen=True, slots=True)
class EnterBakeMode:
    job_id: str
    blender_process_id: int
    frame_start: int
    frame_end: int
    preset_label: str
    requested_topmost: bool = True
    requested_visible: bool = True


@dataclass(frozen=True, slots=True)
class BakeWindowReady:
    job_id: str
    companion_process_id: int
    window_created: bool
    window_visible: bool
    topmost_applied: bool
    transport_ready: bool

    @property
    def ready(self) -> bool:
        return all((self.job_id, self.companion_process_id > 0,
                    self.window_created, self.window_visible,
                    self.topmost_applied, self.transport_ready))


def encode_message(kind: str, token: str, snapshot: BakeSnapshot | None = None,
                   payload: dict | None = None) -> bytes:
    kind = _ALIASES.get(kind, kind)
    if kind not in MESSAGE_TYPES:
        raise ValueError("unsupported message kind")
    data = {"type": kind, "token": token}
    if snapshot is not None:
        data["snapshot"] = snapshot.to_dict()
    if payload is not None:
        data["payload"] = payload
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
    if data["type"] in {"enter_bake_mode", "bake_window_ready",
                        "startup_error"} and not isinstance(
                            data.get("payload"), dict):
        raise ValueError("message payload missing")
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
        # Controller listeners run on Blender's main thread.  Never perform a
        # socket write from publish(): a companion that stopped reading must
        # not freeze Blender while a bake result is being attached.
        self._status_outbox: Queue[bytes] = Queue(maxsize=1)
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
                    self._flush_status(client)
                    try: chunk = client.recv(4096)
                    except socket.timeout: continue
                    if not chunk: break
                    buffer += chunk
                    if len(buffer) > MAX_MESSAGE_BYTES: break
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        try: message = decode_message(raw, self.token)
                        except (ValueError, PermissionError): continue
                        if message["type"] in {"ready", "cancel_request", "close_notice",
                                               "bake_window_ready", "startup_error"}:
                            value = (message if message["type"] in {
                                "bake_window_ready", "startup_error"}
                                else message["type"])
                            try: self.requests.put_nowait(value)
                            except Exception: pass
            finally:
                with self._lock:
                    if self._client is client: self._client = None
                client.close()

    def _flush_status(self, client):
        try:
            data = self._status_outbox.get_nowait()
        except Empty:
            return
        try:
            client.sendall(data)
        except OSError:
            pass

    def _send(self, kind, snapshot=None):
        data = encode_message(kind, self.token, snapshot) + b"\n"
        with self._lock:
            client = self._client
        if client:
            try: client.sendall(data)
            except OSError: pass

    def publish(self, snapshot):
        """Queue only the newest status without ever blocking the caller."""
        data = encode_message("bake_status", self.token, snapshot) + b"\n"
        try:
            self._status_outbox.put_nowait(data)
            return
        except Full:
            pass
        try:
            self._status_outbox.get_nowait()
        except Empty:
            pass
        try:
            self._status_outbox.put_nowait(data)
        except Full:
            pass
    def connected(self):
        with self._lock: return self._client is not None
    def enter_bake_mode(self, request: EnterBakeMode):
        self._send_payload("enter_bake_mode", asdict(request))
    def _send_payload(self, kind, payload):
        data = encode_message(kind, self.token, payload=payload) + b"\n"
        with self._lock: client = self._client
        if client:
            try: client.sendall(data)
            except OSError: pass
    def shutdown_companion(self): self._send("shutdown")
    def poll_request(self):
        try: return self.requests.get_nowait()
        except Empty: return None
    def close(self, *, join: bool = True):
        self._stop.set(); self.shutdown_companion()
        try: self._server.close()
        except OSError: pass
        with self._lock: client=self._client; self._client=None
        if client:
            try: client.close()
            except OSError: pass
        if join:
            self._thread.join(timeout=1)


class LocalSocketClient:
    def __init__(self, port: int, token: str):
        self.token=token; self._socket=socket.create_connection(("127.0.0.1", int(port)), timeout=2)
        self._socket.settimeout(0.2); self._buffer=b""; self.closed=False
    def send(self, kind, payload=None):
        self._socket.sendall(encode_message(kind, self.token, payload=payload)+b"\n")
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
