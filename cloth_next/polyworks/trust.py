# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned entitlement verification keys (contract section 4.2).

Release builds verify entitlements only with the keys pinned below. A key
fetched from the API at runtime is never trusted on its own. Key rotation adds a
new ``kid`` here while the previous one stays pinned for the overlap period.

Development: ``CLOTH_NEXT_POLYWORKS_TRUST_FILE`` may name a JSON file
``{"keys": [{"key_id": "...", "public_key_pem": "..."}]}`` holding the key of an
isolated local backend. Such keys are reported as development trust in every
status view so they can never pass unnoticed, and they cannot replace a pinned
production ``kid``.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from .ed25519 import KeyFormatError, raw_public_key_from_pem

TRUST_FILE_ENV = "CLOTH_NEXT_POLYWORKS_TRUST_FILE"

# kid -> PEM. Populate from GET /v1/entitlements/public-key of the production
# backend once the product owner supplies the approved key. Empty means no
# production entitlement can be verified yet (fails closed).
PRODUCTION_KEYS: dict[str, str] = {}


@dataclass(frozen=True)
class TrustStore:
    keys: dict          # kid -> raw 32-byte key
    development: frozenset  # kids that came from the development trust file

    def key(self, kid: str):
        return self.keys.get(kid)

    def is_development(self, kid: str) -> bool:
        return kid in self.development


def _load_pem_map(pems: dict) -> dict:
    keys = {}
    for kid, pem in pems.items():
        if isinstance(kid, str) and kid:
            keys[kid] = raw_public_key_from_pem(pem)
    return keys


def _development_keys(environ) -> dict:
    name = environ.get(TRUST_FILE_ENV, "")
    if not name:
        return {}
    try:
        path = Path(name)
        if path.stat().st_size > 64 * 1024:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = {}
        for item in payload.get("keys", []):
            kid, pem = item.get("key_id"), item.get("public_key_pem")
            if isinstance(kid, str) and kid and isinstance(pem, str):
                result[kid] = raw_public_key_from_pem(pem)
        return result
    except (OSError, ValueError, AttributeError, KeyFormatError):
        return {}


def load_trust(environ=None, production: dict | None = None) -> TrustStore:
    environ = os.environ if environ is None else environ
    pinned = _load_pem_map(PRODUCTION_KEYS if production is None else production)
    development = {kid: key for kid, key in _development_keys(environ).items()
                   if kid not in pinned}
    return TrustStore({**pinned, **development}, frozenset(development))
