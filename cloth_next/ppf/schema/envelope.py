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
from pathlib import Path
from typing import Any

from . import cbor_codec

SCHEMA_VERSION = 1

KIND_SCENE = "Scene"
KIND_PARAM = "Param"
KIND_VERTEX_MAP = "VertexMap"

_INACTIVE_MOMENTUM_KEY = "inactive-momentum"


class EnvelopeError(ValueError):
    pass


def _payload_for_wire(kind: str, payload: Any) -> Any:
    """Return the payload with PPF startup momentum suppression disabled.

    Upstream represents enabled inactive momentum as a positive scene-level
    duration and converts it into a dynamic hold. The permanently disabled
    representation is omission: PPF's application default is ``False``.
    Strip both possible activation paths at the final Cloth NeXt wire boundary
    without mutating the caller's diagnostic payload.
    """
    if kind != KIND_PARAM or not isinstance(payload, dict):
        return payload

    scene = payload.get("scene")
    dynamic = payload.get("dyn_param")
    static_enabled = (isinstance(scene, dict)
                      and _INACTIVE_MOMENTUM_KEY in scene)
    dynamic_enabled = (isinstance(dynamic, dict)
                       and _INACTIVE_MOMENTUM_KEY in dynamic)
    if not static_enabled and not dynamic_enabled:
        return payload

    wire_payload = dict(payload)
    if isinstance(scene, dict):
        wire_scene = dict(scene)
        wire_scene.pop(_INACTIVE_MOMENTUM_KEY, None)
        wire_payload["scene"] = wire_scene
    if isinstance(dynamic, dict):
        wire_dynamic = dict(dynamic)
        wire_dynamic.pop(_INACTIVE_MOMENTUM_KEY, None)
        if wire_dynamic:
            wire_payload["dyn_param"] = wire_dynamic
        else:
            wire_payload.pop("dyn_param", None)
    return wire_payload


def dumps_envelope(kind: str, payload: Any) -> bytes:
    if kind not in (KIND_SCENE, KIND_PARAM, KIND_VERTEX_MAP):
        raise EnvelopeError(f"unknown payload kind {kind!r}")
    payload = _payload_for_wire(kind, payload)
    return cbor_codec.dumps(
        {"version": SCHEMA_VERSION, "kind": kind, "payload": payload})


def dump_envelope_file(kind: str, payload: Any, path: Path, *,
                       progress=None) -> str:
    """Write a large envelope incrementally and return its wire hash."""
    if kind not in (KIND_SCENE, KIND_PARAM, KIND_VERTEX_MAP):
        raise EnvelopeError(f"unknown payload kind {kind!r}")
    payload = _payload_for_wire(kind, payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()

    class HashingWriter:
        def __init__(self, stream):
            self.stream = stream

        def write(self, chunk):
            digest.update(chunk)
            return self.stream.write(chunk)

    try:
        with path.open("wb") as stream:
            cbor_codec.dump(
                {"version": SCHEMA_VERSION, "kind": kind,
                 "payload": payload},
                HashingWriter(stream), progress=progress)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


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
