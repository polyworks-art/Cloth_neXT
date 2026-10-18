# SPDX-License-Identifier: GPL-3.0-or-later
"""Public account state published by the Companion and read by Blender.

``state.json`` is display information, never authorization: Blender decides
"licensed" only from an entitlement whose signature it verifies itself. The
state can therefore only describe or downgrade (revoked, session expired,
offline) and never upgrade. Writers and readers both pass through ``sanitize``,
an allowlist, so no token or other secret can be published by accident.
"""
from __future__ import annotations

from datetime import datetime, timezone

SCHEMA = "polyworks.state.v1"
MAX_LICENSES = 32
_TEXT_LIMIT = 256

_LICENSE_TEXT = ("id", "product_id", "tier", "purchase_provider", "state", "created_at",
                 "purchase_status", "role", "seat_id")
_LICENSE_INT = ("seats", "devices_active", "device_limit")
SESSION_STATES = ("none", "active", "expired", "unverified")
CHECK_IN_STATES = ("valid", "revoked", "invalid")


def utc_now_iso(now: float | None = None) -> str:
    moment = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _text(value, limit: int = _TEXT_LIMIT):
    if not isinstance(value, str):
        return None
    return "".join(c for c in value[:limit] if c.isprintable()) or None


def _integer(value):
    return value if type(value) is int else None


def _boolean(value):
    return value if isinstance(value, bool) else None


def _choice(value, choices, default=None):
    return value if value in choices else default


def empty_state() -> dict:
    return sanitize({})


def sanitize(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    account = raw.get("account") if isinstance(raw.get("account"), dict) else {}
    connection = raw.get("connection") if isinstance(raw.get("connection"), dict) else {}
    check_in = raw.get("check_in") if isinstance(raw.get("check_in"), dict) else {}
    activation = raw.get("activation") if isinstance(raw.get("activation"), dict) else None
    licenses = raw.get("licenses")
    result = {
        "schema": SCHEMA,
        "written_at": _text(raw.get("written_at"), 40),
        "account": {
            "signed_in": account.get("signed_in") is True,
            "email": _text(account.get("email")),
            "email_verified": _boolean(account.get("email_verified")),
            "session": _choice(account.get("session"), SESSION_STATES, "none"),
            "session_expires_at": _text(account.get("session_expires_at"), 40),
            "storage": _text(account.get("storage"), 40),
        },
        "licenses": None,
        "activation": None,
        "check_in": {
            "status": _choice(check_in.get("status"), CHECK_IN_STATES),
            "reason": _text(check_in.get("reason"), 64),
            "at": _text(check_in.get("at"), 40),
        },
        "connection": {
            "online": _boolean(connection.get("online")),
            "last_success_at": _text(connection.get("last_success_at"), 40),
            "last_attempt_at": _text(connection.get("last_attempt_at"), 40),
            "last_error": _text(connection.get("last_error"), 64),
            "next_check_at": _text(connection.get("next_check_at"), 40),
            "retry_step": max(0, min(_integer(connection.get("retry_step")) or 0, 100)),
        },
        "notice": _text(raw.get("notice"), 64),
    }
    if isinstance(licenses, list):
        result["licenses"] = []
        for item in licenses[:MAX_LICENSES]:
            if not isinstance(item, dict) or not _text(item.get("id")):
                continue
            entry = {name: _text(item.get(name)) for name in _LICENSE_TEXT}
            entry.update({name: _integer(item.get(name)) for name in _LICENSE_INT})
            entry["usable"] = item.get("usable") is True
            result["licenses"].append(entry)
    if activation is not None and _text(activation.get("license_id")):
        result["activation"] = {
            "license_id": _text(activation.get("license_id")),
            "activation_id": _text(activation.get("activation_id")),
            "label": _text(activation.get("label"), 128),
            "activated_at": _text(activation.get("activated_at"), 40),
        }
    return result
