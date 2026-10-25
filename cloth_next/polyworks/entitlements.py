# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline entitlement verification (contract sections 4.1 and 4.3).

Format: ``base64url(payload_json) "." base64url(ed25519_signature)`` without
padding; the signature covers the exact decoded payload bytes. Nothing in the
payload is trusted before the signature has been verified with a pinned key.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import re
import time

from . import ed25519
from .trust import TrustStore

ENTITLEMENT_TYPE = "polyworks.entitlement"
SUPPORTED_VERSION = 1
PRODUCT_ID = "cloth-next"
CLOCK_SKEW_SECONDS = 300  # contract 4.3: iat <= now + 300 and now - 300 <= exp
MAX_TOKEN_BYTES = 16 * 1024
_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")


class EntitlementError(ValueError):
    """``code`` is one of: malformed, unknown_key, bad_signature,
    unsupported_version, wrong_installation, wrong_product, not_yet_valid,
    expired."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(detail or code)


@dataclass(frozen=True)
class Entitlement:
    key_id: str
    license_id: str
    subject: str
    activation_id: str
    installation_id: str
    product: str
    tier: str
    seats: int | None
    seat_id: str
    issued_at: int
    expires_at: int
    development_key: bool = False

    def seconds_remaining(self, now: float) -> float:
        return self.expires_at - now


def _decode(segment: str) -> bytes:
    if not _SEGMENT.fullmatch(segment):
        raise EntitlementError("malformed", "invalid base64url segment")
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError) as exc:
        raise EntitlementError("malformed", "invalid base64url") from exc
    if base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != segment:
        raise EntitlementError("malformed", "non-canonical base64url")
    return raw


def _text(payload: dict, name: str, *, required: bool = True) -> str:
    value = payload.get(name)
    if isinstance(value, str) and value:
        return value
    if required:
        raise EntitlementError("malformed", f"missing {name}")
    return ""


def _integer(payload: dict, name: str) -> int:
    value = payload.get(name)
    if type(value) is not int:
        raise EntitlementError("malformed", f"invalid {name}")
    return value


def verify(token: str, *, trust: TrustStore, installation_id: str,
           now: float | None = None, check_time: bool = True) -> Entitlement:
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES:
        raise EntitlementError("malformed", "invalid token size")
    payload_part, separator, signature_part = token.strip().partition(".")
    if not separator or "." in signature_part:
        raise EntitlementError("malformed", "expected payload.signature")
    payload_bytes = _decode(payload_part)
    signature = _decode(signature_part)
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EntitlementError("malformed", "payload is not JSON") from exc
    if not isinstance(payload, dict):
        raise EntitlementError("malformed", "payload is not an object")
    kid = payload.get("kid")
    key = trust.key(kid) if isinstance(kid, str) else None
    if key is None:
        raise EntitlementError("unknown_key", "no pinned key for this kid")
    if len(signature) != 64 or not ed25519.verify(key, payload_bytes, signature):
        raise EntitlementError("bad_signature", "signature does not verify")
    # Only now are the payload claims authentic.
    if payload.get("typ") != ENTITLEMENT_TYPE:
        raise EntitlementError("malformed", "unexpected typ")
    if payload.get("v") != SUPPORTED_VERSION or type(payload.get("v")) is not int:
        raise EntitlementError("unsupported_version", "unknown entitlement version")
    if payload.get("inst") != installation_id:
        raise EntitlementError("wrong_installation", "issued for another installation")
    if payload.get("prd") != PRODUCT_ID:
        raise EntitlementError("wrong_product", "issued for another product")
    issued, expires = _integer(payload, "iat"), _integer(payload, "exp")
    seats = payload.get("seats")
    entitlement = Entitlement(
        key_id=kid, license_id=_text(payload, "lic"), subject=_text(payload, "sub"),
        activation_id=_text(payload, "act"), installation_id=installation_id,
        product=PRODUCT_ID, tier=_text(payload, "tier"),
        seats=seats if type(seats) is int else None,
        seat_id=_text(payload, "seat", required=False),
        issued_at=issued, expires_at=expires,
        development_key=trust.is_development(kid))
    if check_time:
        problem = time_problem(entitlement, time.time() if now is None else now)
        if problem:
            raise EntitlementError(problem, problem.replace("_", " "))
    return entitlement


FEATURE_ENTITLEMENT_TYPE = "polyworks.feature-entitlement"
GAIA_FEATURE = "gaia"


@dataclass(frozen=True)
class FeatureEntitlement:
    key_id: str
    subject: str
    installation_id: str
    features: tuple
    source: str
    issued_at: int
    expires_at: int


def verify_gaia(token: str, *, trust: TrustStore, installation_id: str,
                now: float | None = None) -> FeatureEntitlement:
    """Same rules the native TETHYS Engine applies; used to refuse storing a
    token the engine would reject. A licence entitlement never qualifies."""
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_BYTES:
        raise EntitlementError("malformed", "invalid token size")
    payload_part, separator, signature_part = token.strip().partition(".")
    if not separator or "." in signature_part:
        raise EntitlementError("malformed", "expected payload.signature")
    payload_bytes, signature = _decode(payload_part), _decode(signature_part)
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EntitlementError("malformed", "payload is not JSON") from exc
    if not isinstance(payload, dict):
        raise EntitlementError("malformed", "payload is not an object")
    kid = payload.get("kid")
    key = trust.key(kid) if isinstance(kid, str) else None
    if key is None:
        raise EntitlementError("unknown_key", "no pinned key for this kid")
    if len(signature) != 64 or not ed25519.verify(key, payload_bytes, signature):
        raise EntitlementError("bad_signature", "signature does not verify")
    if payload.get("typ") != FEATURE_ENTITLEMENT_TYPE:
        raise EntitlementError("malformed", "not a feature entitlement")
    if payload.get("v") != 1 or type(payload.get("v")) is not int:
        raise EntitlementError("unsupported_version", "unknown entitlement version")
    if payload.get("prd") != PRODUCT_ID:
        raise EntitlementError("wrong_product", "issued for another product")
    if payload.get("inst") != installation_id:
        raise EntitlementError("wrong_installation", "issued for another installation")
    features = payload.get("feat")
    if not isinstance(features, list) or GAIA_FEATURE not in features:
        raise EntitlementError("malformed", "no gaia feature")
    issued, expires = _integer(payload, "iat"), _integer(payload, "exp")
    result = FeatureEntitlement(kid, _text(payload, "sub"), installation_id,
                                tuple(f for f in features if isinstance(f, str)),
                                _text(payload, "src", required=False), issued, expires)
    now = time.time() if now is None else now
    if issued > now + CLOCK_SKEW_SECONDS:
        raise EntitlementError("not_yet_valid", "not yet valid")
    if now - CLOCK_SKEW_SECONDS > expires:
        raise EntitlementError("expired", "expired")
    return result


def time_problem(entitlement: Entitlement, now: float) -> str | None:
    """Contract 4.3 time window. A wall clock set back before ``iat`` fails
    closed as ``not_yet_valid``; no further clock policy is invented."""
    if entitlement.issued_at > now + CLOCK_SKEW_SECONDS:
        return "not_yet_valid"
    if now - CLOCK_SKEW_SECONDS > entitlement.expires_at:
        return "expired"
    return None
