# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The request body of ``POST /v1/telemetry`` (schema 1). No ``bpy``, no network.

Contract (proposed to the server, 2026-11-06): one package per request with
at most 200 events and 256 KB; unknown fields are rejected by the server.
Events are ``perf``, ``bake``, ``error`` and ``crash``. Identical errors are
merged into one event with a ``count``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

PATH = "/v1/telemetry"
SCHEMA = 1
MAX_EVENTS = 200
MAX_BYTES = 256 * 1024
MESSAGE_LIMIT = 500
TRACE_LIMIT = 20000
EVENT_TYPES = ("perf", "bake", "error", "crash")
ENVIRONMENT_FIELDS = {"addon_version": 64, "blender_version": 64,
                      "operating_system": 128, "gpu": 160}

# Allowed keys per event type; anything else is removed before sending.
EVENT_FIELDS = {
    "perf": ("type", "at", "cpu_percent", "ram_used_mb", "ram_total_mb", "gpu_percent",
             "vram_used_mb", "vram_total_mb", "bake_active"),
    "bake": ("type", "at", "engine", "engine_version", "backend", "result", "error_code",
             "frames", "seconds", "seconds_per_frame", "execution"),
    "error": ("type", "at", "code", "logger", "level", "message", "count"),
    "crash": ("type", "at", "source", "kind", "trace"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _allowed(event: dict) -> dict | None:
    kind = event.get("type")
    if kind not in EVENT_FIELDS:
        return None
    return {key: event[key] for key in EVENT_FIELDS[kind] if event.get(key) is not None}


def _merge_key(event: dict):
    if event.get("type") != "error":
        return None
    return (event.get("code"), event.get("logger"), event.get("level"), event.get("message"))


def build_package(identity: dict, events: list[dict]) -> tuple[dict, int]:
    """Return the request body and how many queued events it consumed.

    Consumes events in queue order until 200 merged events or 256 KB are
    reached, so the caller can drop exactly that many after a 202.
    """
    package = {"schema": SCHEMA,
               "hardware_id": identity["hardware_id"],
               "session_id": identity["session_id"]}
    for field, limit in ENVIRONMENT_FIELDS.items():
        value = str(identity.get(field) or "")[:limit]
        if value:
            package[field] = value
    merged: list[dict] = []
    index_by_key: dict = {}
    base_size = len(json.dumps({**package, "events": []}, separators=(",", ":")))
    size = base_size
    consumed = 0
    for event in events:
        clean = _allowed(event) if isinstance(event, dict) else None
        if clean is None:
            consumed += 1
            continue
        key = _merge_key(clean)
        if key is not None and key in index_by_key:
            target = merged[index_by_key[key]]
            target["count"] = int(target.get("count", 1)) + int(clean.get("count", 1))
            consumed += 1
            continue
        encoded = len(json.dumps(clean, separators=(",", ":"))) + 1
        if len(merged) >= MAX_EVENTS or size + encoded > MAX_BYTES:
            break
        if key is not None:
            clean.setdefault("count", 1)
            index_by_key[key] = len(merged)
        merged.append(clean)
        size += encoded
        consumed += 1
    package["events"] = merged
    return package, consumed
