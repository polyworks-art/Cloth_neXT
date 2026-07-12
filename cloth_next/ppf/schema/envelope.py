# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Schema-version envelope wrapped around every PPF cross-language payload.

Mirror of ``crates/ppf-cts-formats/src/envelope.rs`` at pinned commit
``7193f158``: ``{"version": 1, "kind": <kind>, "payload": <payload>}``
encoded as CBOR. Consumers reject a version or kind mismatch, so this module
does the same on decode.
"""

from __future__ import annotations

import hashlib
from typing import Any

from . import cbor_codec

SCHEMA_VERSION = 1

KIND_SCENE = "Scene"
KIND_PARAM = "Param"
KIND_VERTEX_MAP = "VertexMap"


class EnvelopeError(ValueError):
    pass


def dumps_envelope(kind: str, payload: Any) -> bytes:
    if kind not in (KIND_SCENE, KIND_PARAM, KIND_VERTEX_MAP):
        raise EnvelopeError(f"unknown payload kind {kind!r}")
    return cbor_codec.dumps(
        {"version": SCHEMA_VERSION, "kind": kind, "payload": payload})


def loads_envelope(blob: bytes, expected_kind: str) -> Any:
    envelope = cbor_codec.loads(blob)
    if not isinstance(envelope, dict):
        raise EnvelopeError("envelope must be a CBOR map")
    version = envelope.get("version")
    if version != SCHEMA_VERSION:
        raise EnvelopeError(f"schema version mismatch: payload={version!r}, "
                            f"expected {SCHEMA_VERSION}")
    kind = envelope.get("kind")
    if kind != expected_kind:
        raise EnvelopeError(f"payload kind mismatch: payload={kind!r}, "
                            f"expected {expected_kind!r}")
    if "payload" not in envelope:
        raise EnvelopeError("envelope missing 'payload' key")
    return envelope["payload"]


def payload_sha256(blob: bytes) -> str:
    """SHA-256 of the encoded envelope bytes (the upload/status hash)."""
    return hashlib.sha256(blob).hexdigest()
