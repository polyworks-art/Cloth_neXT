# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded newline-framed JSON protocol used by the external worker."""

from __future__ import annotations

import json

MAX_MESSAGE_BYTES = 64 * 1024 * 1024
COMMANDS = frozenset({"health", "capabilities", "create_preview",
                      "update_target_frame", "pause", "reset",
                      "restore_snapshot", "update_parameters", "status",
                      "cancel", "destroy_preview", "shutdown"})


def encode_message(value: dict) -> bytes:
    encoded = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ValueError("Newton worker message exceeds the protocol limit")
    return encoded


def decode_message(value: bytes) -> dict:
    if not value or len(value) > MAX_MESSAGE_BYTES or not value.endswith(b"\n"):
        raise ValueError("invalid Newton worker message framing")
    decoded = json.loads(value.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Newton worker message must be a JSON object")
    return decoded


def command_message(command: str, **payload) -> dict:
    if command not in COMMANDS:
        raise ValueError(f"unsupported Newton worker command: {command}")
    return {"command": command, **payload}
