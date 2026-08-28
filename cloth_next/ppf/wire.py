# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Typed supported-PPF wire operations: TCMD lifecycle requests, atomic scene
upload, and bounded file retrieval.

Framing verified against the pinned upstream server sources
(``crates/ppf-cts-server/src/protocol.rs``, ``wire/mod.rs``, ``wire/upload.rs``,
``wire/data.rs`` at commit ``7193f158``) and the official client
(``blender_addon/core/protocol.py``):

- ``TCMD``: 4 ASCII header bytes, 4-byte big-endian payload length, then the
  UTF-8 ``--key value`` argument string; response is one newline-terminated
  JSON object.
- ``JSON``: 4 ASCII header bytes, one newline-terminated JSON header line.
  ``upload_atomic`` then streams the raw data and param payloads back-to-back
  and the server answers ``OK\\n`` (or a JSON error line). ``data_receive``
  answers with a ``{"size": N}`` JSON line followed by exactly ``N`` raw
  bytes (or a ``{"error": ...}`` line).

Free-form command strings never appear outside this module; callers use the
typed helpers below. The existing side-effect-free status ping stays in
``transport.py`` untouched.
"""

from __future__ import annotations

import json
import os
import errno
from pathlib import Path
import socket
from dataclasses import dataclass

from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord
from .transport import TransportConfig

TCMD_HEADER = b"TCMD"
JSON_HEADER = b"JSON"
# One MiB amortizes Python and socket overhead for multi-gigabyte deforming
# collider scenes while remaining comfortably bounded for frame downloads.
# Smaller bounded writes reduce peak buffering in the Windows server process
# for dense animated-Collider payloads without changing protocol framing.
CHUNK_SIZE = 256 * 1024
MAX_JSON_LINE = 64 * 1024
# Bound on one received file (map.pickle / vert_<N>.bin). The vertical-slice
# meshes are tiny; 256 MiB leaves room for future scenes while still
# rejecting absurd declared sizes outright.
MAX_RECEIVE_BYTES = 256 * 1024 * 1024

# The exact TCMD request spellings accepted by the audited server
# (wire/mod.rs::tcmd_request_to_event).
REQUEST_BUILD = "build"
REQUEST_CANCEL_BUILD = "cancel_build"
REQUEST_START = "start"
REQUEST_RESUME = "resume"
REQUEST_TERMINATE = "terminate"
REQUEST_SAVE_AND_QUIT = "save_and_quit"
REQUEST_DELETE = "delete"
_KNOWN_REQUESTS = frozenset({REQUEST_BUILD, REQUEST_CANCEL_BUILD,
                             REQUEST_START, REQUEST_RESUME, REQUEST_TERMINATE,
                             REQUEST_SAVE_AND_QUIT, REQUEST_DELETE})


class WireProtocolError(ClothNextError):
    pass


def _error(message: str, technical: str,
           exc: BaseException | None = None, *,
           failure_phase: str = "INVALID_RESPONSE") -> WireProtocolError:
    return WireProtocolError(ErrorRecord.create(
        category=ErrorCategory.SOLVER_CONNECTION,
        user_message=message,
        technical_message=(
            f"transport_failure_phase={failure_phase}; {technical}"),
        recommended_action="Check the solver process, its logs, and the "
                           "connection, then retry.",
        recoverable=True,
        context={"transport_failure_phase": failure_phase}, exception=exc,
    ))


def _validate_project_name(project_name: str) -> str:
    if not project_name or any(ch.isspace() for ch in project_name):
        raise ValueError("PPF project name must be non-empty without whitespace")
    return project_name


def tcmd_request_bytes(project_name: str, request: str | None = None, *,
                       frame: int | None = None) -> bytes:
    """Exact TCMD frame: header + u32 BE length + ``--name`` [+ ``--request``]."""
    _validate_project_name(project_name)
    payload = f"--name {project_name}"
    if request is not None:
        if request not in _KNOWN_REQUESTS:
            raise ValueError(f"unknown TCMD request {request!r}")
        payload += f" --request {request}"
    if frame is not None:
        if request != REQUEST_RESUME or int(frame) < 0:
            raise ValueError("frame is valid only for a non-negative resume")
        payload += f" --frame {int(frame)}"
    raw = payload.encode("utf-8")
    return TCMD_HEADER + len(raw).to_bytes(4, "big") + raw


@dataclass(frozen=True, slots=True)
class ServerAddress:
    host: str
    port: int

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("host must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")


def _connect(address: ServerAddress, config: TransportConfig) -> socket.socket:
    try:
        connection = socket.create_connection(
            (address.host, address.port), timeout=config.connect_timeout)
    except (TimeoutError, socket.timeout) as exc:
        raise _error(
            "The solver did not accept a connection in time.",
            f"connect timed out at {address.host}:{address.port}", exc,
            failure_phase="CONNECT_TIMEOUT") from exc
    except OSError as exc:
        code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
        if code in {errno.ECONNREFUSED, 10061}:
            phase = "CONNECTION_REFUSED"
        elif code in {errno.ECONNRESET, 10054}:
            phase = "CONNECTION_RESET"
        else:
            phase = "CONNECT_ERROR"
        raise _error("Could not connect to the solver.",
                     f"connect failed at {address.host}:{address.port}: {exc}",
                     exc, failure_phase=phase) from exc
    connection.settimeout(config.read_timeout)
    return connection


def _send_all(connection: socket.socket, data: bytes) -> None:
    try:
        connection.sendall(data)
    except OSError as exc:
        phase = ("CONNECTION_RESET" if (
            getattr(exc, "winerror", None) == 10054
            or getattr(exc, "errno", None) == errno.ECONNRESET)
            else "SEND_ERROR")
        raise _error("The connection to the solver broke while sending.",
                     f"sendall failed: {exc}", exc,
                     failure_phase=phase) from exc


def _read_line(connection: socket.socket, *, max_bytes: int = MAX_JSON_LINE,
               initial: bytes = b"") -> tuple[bytes, bytes]:
    """Read until the first newline; returns (line, remainder)."""
    buffer = bytearray(initial)
    while b"\n" not in buffer:
        if len(buffer) > max_bytes:
            raise _error("The solver response line was too large.",
                         f"response line exceeded {max_bytes} bytes")
        try:
            chunk = connection.recv(4096)
        except (TimeoutError, socket.timeout) as exc:
            raise _error("The solver did not respond in time.",
                         "timed out waiting for a response line", exc,
                         failure_phase="READ_TIMEOUT") from exc
        except OSError as exc:
            phase = ("CONNECTION_RESET" if (
                getattr(exc, "winerror", None) == 10054
                or getattr(exc, "errno", None) == errno.ECONNRESET)
                else "READ_ERROR")
            raise _error("The connection to the solver broke.",
                         f"recv failed: {exc}", exc,
                         failure_phase=phase) from exc
        if not chunk:
            raise _error("The solver closed the connection unexpectedly.",
                         "connection closed before the response line completed",
                         failure_phase="CONNECTION_RESET")
        buffer.extend(chunk)
    position = buffer.find(b"\n")
    return bytes(buffer[:position]), bytes(buffer[position + 1:])


def _parse_json_line(line: bytes) -> dict:
    try:
        parsed = json.loads(line.decode("utf-8").strip())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("The solver returned an invalid response.",
                     f"malformed JSON response line: {line[:120]!r}",
                     exc, failure_phase="INVALID_RESPONSE") from exc
    if not isinstance(parsed, dict):
        raise _error("The solver returned an invalid response.",
                     "JSON response is not an object")
    return parsed


def _reject_server_error(parsed: dict, operation: str) -> dict:
    error_value = parsed.get("error")
    if error_value:
        raise _error(f"The solver rejected the {operation} request.",
                     f"server error during {operation}: {error_value}")
    return parsed


def send_tcmd(address: ServerAddress, config: TransportConfig,
              project_name: str, request: str | None = None, *,
              frame: int | None = None,
              allow_server_error: bool = False) -> dict:
    """Send one TCMD frame and return the parsed JSON status response."""
    frame_bytes = tcmd_request_bytes(
        project_name, request, frame=frame)
    with _connect(address, config) as connection:
        _send_all(connection, frame_bytes)
        line, _rest = _read_line(connection, max_bytes=config.max_response_bytes)
    label = request or "status"
    parsed = _parse_json_line(line)
    return parsed if allow_server_error else _reject_server_error(parsed, label)


def upload_atomic(address: ServerAddress, config: TransportConfig, *,
                  project_name: str, data_payload, param_payload,
                  data_hash: str, param_hash: str,
                  file_chunk_size: int = CHUNK_SIZE,
                  use_sendfile: bool = False) -> None:
    """Stream both payloads through the server-side atomic upload."""
    _validate_project_name(project_name)
    if file_chunk_size <= 0:
        raise ValueError("file_chunk_size must be positive")

    def payload_size(payload) -> int:
        return os.path.getsize(payload) if isinstance(payload, (str, Path)) else len(payload)

    def payload_chunks(payload, *, offset=0):
        if isinstance(payload, (str, Path)):
            with open(payload, "rb") as stream:
                stream.seek(offset)
                while chunk := stream.read(file_chunk_size):
                    yield chunk
        else:
            for position in range(0, len(payload), file_chunk_size):
                yield payload[position:position + file_chunk_size]

    def send_file(connection, payload):
        sent = 0
        if use_sendfile and isinstance(payload, (str, Path)):
            try:
                with open(payload, "rb") as stream:
                    size = os.fstat(stream.fileno()).st_size
                    while sent < size:
                        count = connection.sendfile(
                            stream, offset=sent, count=size - sent)
                        if not count:
                            raise OSError("socket.sendfile made no progress")
                        sent += count
                return
            except (AttributeError, NotImplementedError, OSError):
                # Resume from the confirmed offset; never resend bytes already
                # accepted by the socket.
                pass
        for chunk in payload_chunks(payload, offset=sent):
            _send_all(connection, chunk)

    data_size, param_size = payload_size(data_payload), payload_size(param_payload)
    if not data_size or not param_size:
        raise ValueError("the vertical slice always uploads both payloads")
    header = json.dumps({
        "request": "upload_atomic",
        "name": project_name,
        # Accepted for protocol compatibility; the server resolves the real
        # project root itself (wire/upload.rs).
        "path": "",
        "data_size": data_size,
        "param_size": param_size,
        "data_hash": data_hash,
        "param_hash": param_hash,
    }).encode("utf-8") + b"\n"
    with _connect(address, config) as connection:
        # Large local Scene/Pin payloads may legitimately keep one blocking
        # send active longer than a status response. Do not reuse the short
        # request/response timeout for the upload body.
        connection.settimeout(config.upload_write_timeout)
        _send_all(connection, JSON_HEADER + header)
        for payload in (data_payload, param_payload):
            if isinstance(payload, (str, Path)):
                send_file(connection, payload)
            else:
                for chunk in payload_chunks(payload):
                    _send_all(connection, chunk)
        connection.settimeout(config.read_timeout)
        line, _rest = _read_line(connection)
    if b"OK" not in line:
        parsed = None
        try:
            parsed = _parse_json_line(line)
        except ClothNextError:
            pass
        if parsed is not None:
            _reject_server_error(parsed, "upload")
        raise _error("The solver did not confirm the upload.",
                     f"unexpected upload response: {line[:120]!r}")


def data_receive(address: ServerAddress, config: TransportConfig, *,
                 project_name: str, path: str,
                 max_bytes: int = MAX_RECEIVE_BYTES) -> bytearray:
    """Fetch one project-relative file (``session/map.pickle``,
    ``session/output/vert_<N>.bin``) with bounded size."""
    _validate_project_name(project_name)
    if not path or path.startswith(("/", "\\")) or ".." in path:
        raise ValueError(f"unsafe data_receive path {path!r}")
    header = json.dumps({
        "request": "data_receive",
        "name": project_name,
        "path": path,
    }).encode("utf-8") + b"\n"
    with _connect(address, config) as connection:
        _send_all(connection, JSON_HEADER + header)
        line, remainder = _read_line(connection)
        metadata = _reject_server_error(_parse_json_line(line),
                                        f"data_receive {path}")
        size = metadata.get("size")
        if not isinstance(size, int) or size < 0:
            raise _error("The solver returned an invalid response.",
                         f"data_receive metadata has no valid size: {metadata}")
        if size > max_bytes:
            raise _error("The solver response was too large.",
                         f"declared size {size} exceeds bound {max_bytes}")
        payload = bytearray(size)
        initial = remainder[:size]
        payload[:len(initial)] = initial
        received = len(initial)
        view = memoryview(payload)
        while received < size:
            try:
                count = connection.recv_into(
                    view[received:], min(CHUNK_SIZE, size - received))
            except (TimeoutError, socket.timeout) as exc:
                raise _error("The solver did not respond in time.",
                             f"timed out mid-transfer of {path} "
                             f"({received}/{size} bytes)", exc,
                             failure_phase="READ_TIMEOUT") from exc
            except OSError as exc:
                raise _error("The connection to the solver broke.",
                             f"recv failed mid-transfer of {path}: {exc}",
                             exc, failure_phase="CONNECTION_RESET") from exc
            if not count:
                raise _error("The solver closed the connection mid-transfer.",
                             f"truncated transfer of {path}: "
                             f"{received} of {size} bytes")
            received += count
        view.release()
    return payload
