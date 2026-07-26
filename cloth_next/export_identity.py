# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent object identities and deterministic solver UUIDs."""

from __future__ import annotations

import hashlib
import uuid

EXPORT_UUID_SCHEMA_VERSION = 1


def new_persistent_id() -> str:
    return uuid.uuid4().hex


def ensure_persistent_id(obj, *, occupied=()) -> str:
    settings = obj.cloth_next
    value = str(getattr(settings, "persistent_export_id", "") or "").strip()
    occupied = set(occupied)
    if not value or value in occupied:
        value = new_persistent_id()
        while value in occupied:
            value = new_persistent_id()
        settings.persistent_export_id = value
    return value


def ensure_unique_persistent_ids(objects) -> tuple[tuple[object, str], ...]:
    result = []
    occupied = set()
    for obj in objects:
        value = ensure_persistent_id(obj, occupied=occupied)
        occupied.add(value)
        result.append((obj, value))
    return tuple(result)


def export_uuid_from_identity(persistent_id: str, role: str) -> str:
    if not persistent_id or not role:
        raise ValueError("persistent identity and role are required")
    payload = (
        f"cloth-next-export-uuid\0{EXPORT_UUID_SCHEMA_VERSION}\0"
        f"{persistent_id}\0{role}"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return str(uuid.UUID(digest[:32]))


def export_uuid(obj) -> str:
    settings = obj.cloth_next
    return export_uuid_from_identity(
        str(settings.persistent_export_id), str(settings.role))
