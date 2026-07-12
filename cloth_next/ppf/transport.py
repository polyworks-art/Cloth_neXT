# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Minimal PPF 0.11 TCMD status transport. No other operations live here."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass

from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord

TCMD_HEADER = b"TCMD"
MAX_STATUS_RESPONSE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class TransportConfig:
    connect_timeout: float = 2.0
    read_timeout: float = 2.0
    max_response_bytes: int = MAX_STATUS_RESPONSE

    def __post_init__(self) -> None:
        if self.connect_timeout <= 0 or self.read_timeout <= 0:
            raise ValueError("timeouts must be positive")
        if not 1 <= self.max_response_bytes <= 16 * 1024 * 1024:
            raise ValueError("max_response_bytes is outside the safe range")


def status_request_bytes(project_name: str) -> bytes:
    if not project_name or any(ch.isspace() for ch in project_name):
        raise ValueError("PPF project name must be non-empty and contain no whitespace")
    payload = f"--name {project_name}".encode("utf-8")
    return TCMD_HEADER + len(payload).to_bytes(4, "big") + payload


def _transport_error(message: str, technical: str, exc: BaseException | None = None) -> ClothNextError:
    return ClothNextError(ErrorRecord.create(
        category=ErrorCategory.SOLVER_CONNECTION,
        user_message=message,
        technical_message=technical,
        recommended_action="Check the solver address, port, logs, and firewall, then retry.",
        recoverable=True,
        exception=exc,
    ))


def query_status(host: str, port: int, project_name: str, config: TransportConfig) -> dict[str, object]:
    request = status_request_bytes(project_name)
    chunks: list[bytes] = []
    total = 0
    try:
        with socket.create_connection((host, port), timeout=config.connect_timeout) as connection:
            connection.settimeout(config.read_timeout)
            connection.sendall(request)
            while True:
                chunk = connection.recv(min(32 * 1024, config.max_response_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > config.max_response_bytes:
                    raise _transport_error("The solver response was too large.", f"response exceeded {config.max_response_bytes} bytes")
    except ClothNextError:
        raise
    except (TimeoutError, socket.timeout) as exc:
        raise _transport_error("The solver did not respond in time.", f"PPF status timeout at {host}:{port}", exc) from exc
    except OSError as exc:
        raise _transport_error("Could not connect to the solver.", f"PPF connection failed at {host}:{port}: {exc}", exc) from exc
    raw = b"".join(chunks).rstrip(b"\r\n")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _transport_error("The service returned an invalid response.", "PPF status response is not UTF-8", exc) from exc
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _transport_error("The service returned an invalid response.", "PPF status response is not JSON", exc) from exc
    if not isinstance(result, dict):
        raise _transport_error("The service returned an invalid response.", "PPF status response is not a JSON object")
    return result

