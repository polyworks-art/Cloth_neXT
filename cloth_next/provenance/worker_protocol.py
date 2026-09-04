# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded authenticated loopback protocol for the owned ThreadMark worker."""

from __future__ import annotations

import json
import socket

MAX_MESSAGE_BYTES = 16 * 1024


def send_message(connection: socket.socket, payload: dict) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("ThreadMark worker message exceeds the bounded limit")
    connection.sendall(data)


def receive_message(connection: socket.socket) -> dict:
    data = bytearray()
    while len(data) < MAX_MESSAGE_BYTES:
        chunk = connection.recv(min(4096, MAX_MESSAGE_BYTES - len(data)))
        if not chunk:
            raise ConnectionError("ThreadMark worker closed the connection")
        data.extend(chunk)
        newline = data.find(b"\n")
        if newline >= 0:
            if data[newline + 1 :]:
                raise ValueError("ThreadMark worker sent trailing message data")
            payload = json.loads(data[:newline].decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("ThreadMark worker message must be an object")
            return payload
    raise ValueError("ThreadMark worker message exceeds the bounded limit")
