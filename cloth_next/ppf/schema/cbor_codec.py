# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Strict CBOR subset codec for the verified PPF 0.11 wire payloads.

This is deliberately NOT a general CBOR library. It implements exactly the
subset the pinned PPF protocol uses (RFC 8949 major types 0-5 and 7), with
the same output choices ``cbor2`` makes for the payloads the official addon
produces, so the encoded bytes are accepted byte-for-byte by the shipped
solver frontend (``cbor2.loads``) and the Rust ``ciborium`` reader:

- unsigned/negative integers in their shortest form,
- Python ``float`` always as IEEE-754 float64 (major 7, ai 27) — ``cbor2``
  emits doubles for Python floats by default and the Rust schema declares
  every wire float as ``f64``,
- definite-length UTF-8 text strings, byte strings, arrays, and maps,
- ``False`` / ``True`` / ``None`` simple values,
- map insertion order preserved (payload hashes must be stable).

Everything else (tags, indefinite lengths, bignums, half/single floats on
encode) is rejected loudly. Decoding additionally accepts float16/float32
for robustness against future producers, but never silently skips data.

Runtime dependency decision (Phase 3A): no third-party wheel is vendored
into the extension. The subset above is small, closed, and pinned by the
protocol audit; correctness is enforced by golden fixtures generated with
the exact ``cbor2`` build shipped inside the official solver package (see
``tests/test_ppf_cbor_codec.py`` and ``tests/fixtures/ppf_0_11/``).
"""

from __future__ import annotations

import struct
from typing import Any

MAX_DECODE_ITEMS = 50_000_000  # hard bound: no payload we decode is larger
MAX_NESTING = 32


class CborError(ValueError):
    """Raised on any unsupported construct or malformed input."""


# ---------------------------------------------------------------------------
# Encoding

def _encode_head(major: int, argument: int, out: bytearray) -> None:
    if argument < 0:
        raise CborError("negative length argument")
    if argument < 24:
        out.append((major << 5) | argument)
    elif argument < 0x100:
        out.append((major << 5) | 24)
        out.append(argument)
    elif argument < 0x1_0000:
        out.append((major << 5) | 25)
        out.extend(argument.to_bytes(2, "big"))
    elif argument < 0x1_0000_0000:
        out.append((major << 5) | 26)
        out.extend(argument.to_bytes(4, "big"))
    elif argument < 0x1_0000_0000_0000_0000:
        out.append((major << 5) | 27)
        out.extend(argument.to_bytes(8, "big"))
    else:
        raise CborError("integer exceeds 64-bit CBOR argument range")


def _encode_item(value: Any, out: bytearray, depth: int) -> None:
    if depth > MAX_NESTING:
        raise CborError("value nests deeper than the protocol subset allows")
    # bool must be tested before int (bool is an int subclass)
    if value is False:
        out.append(0xF4)
    elif value is True:
        out.append(0xF5)
    elif value is None:
        out.append(0xF6)
    elif isinstance(value, int):
        if value >= 0:
            _encode_head(0, value, out)
        else:
            _encode_head(1, -1 - value, out)
    elif isinstance(value, float):
        out.append(0xFB)
        out.extend(struct.pack(">d", value))
    elif isinstance(value, str):
        raw = value.encode("utf-8")
        _encode_head(3, len(raw), out)
        out.extend(raw)
    elif isinstance(value, (bytes, bytearray)):
        _encode_head(2, len(value), out)
        out.extend(value)
    elif isinstance(value, (list, tuple)):
        _encode_head(4, len(value), out)
        for item in value:
            _encode_item(item, out, depth + 1)
    elif isinstance(value, dict):
        _encode_head(5, len(value), out)
        for key, item in value.items():
            if not isinstance(key, (str, int)) or isinstance(key, bool):
                raise CborError(f"unsupported map key type {type(key).__name__}")
            _encode_item(key, out, depth + 1)
            _encode_item(item, out, depth + 1)
    else:
        raise CborError(f"unsupported type {type(value).__name__} "
                        "(outside the verified PPF wire subset)")


def dumps(value: Any) -> bytes:
    """Encode ``value`` using the strict verified subset."""
    out = bytearray()
    _encode_item(value, out, 0)
    return bytes(out)


# ---------------------------------------------------------------------------
# Decoding

class _Reader:
    __slots__ = ("data", "offset")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.offset + count > len(self.data):
            raise CborError("truncated CBOR input")
        chunk = self.data[self.offset:self.offset + count]
        self.offset += count
        return chunk

    def byte(self) -> int:
        return self.take(1)[0]


def _decode_argument(reader: _Reader, additional: int) -> int:
    if additional < 24:
        return additional
    if additional == 24:
        return reader.byte()
    if additional == 25:
        return int.from_bytes(reader.take(2), "big")
    if additional == 26:
        return int.from_bytes(reader.take(4), "big")
    if additional == 27:
        return int.from_bytes(reader.take(8), "big")
    raise CborError(f"unsupported additional info {additional} "
                    "(indefinite lengths are outside the subset)")


def _decode_item(reader: _Reader, depth: int) -> Any:
    if depth > MAX_NESTING:
        raise CborError("input nests deeper than the protocol subset allows")
    initial = reader.byte()
    major = initial >> 5
    additional = initial & 0x1F
    if major == 0:
        return _decode_argument(reader, additional)
    if major == 1:
        return -1 - _decode_argument(reader, additional)
    if major == 2:
        length = _decode_argument(reader, additional)
        if length > MAX_DECODE_ITEMS:
            raise CborError("byte string exceeds the decode bound")
        return bytes(reader.take(length))
    if major == 3:
        length = _decode_argument(reader, additional)
        if length > MAX_DECODE_ITEMS:
            raise CborError("text string exceeds the decode bound")
        try:
            return reader.take(length).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CborError(f"invalid UTF-8 text string: {exc}") from exc
    if major == 4:
        count = _decode_argument(reader, additional)
        if count > MAX_DECODE_ITEMS:
            raise CborError("array exceeds the decode bound")
        return [_decode_item(reader, depth + 1) for _ in range(count)]
    if major == 5:
        count = _decode_argument(reader, additional)
        if count > MAX_DECODE_ITEMS:
            raise CborError("map exceeds the decode bound")
        result: dict[Any, Any] = {}
        for _ in range(count):
            key = _decode_item(reader, depth + 1)
            if not isinstance(key, (str, int)) or isinstance(key, bool):
                raise CborError(f"unsupported map key type {type(key).__name__}")
            result[key] = _decode_item(reader, depth + 1)
        return result
    if major == 7:
        if additional == 20:
            return False
        if additional == 21:
            return True
        if additional == 22:
            return None
        if additional == 25:
            return struct.unpack(">e", reader.take(2))[0]
        if additional == 26:
            return struct.unpack(">f", reader.take(4))[0]
        if additional == 27:
            return struct.unpack(">d", reader.take(8))[0]
        raise CborError(f"unsupported simple/float encoding {additional}")
    raise CborError(f"unsupported CBOR major type {major} "
                    "(tags are outside the verified subset)")


def loads(data: bytes) -> Any:
    """Decode a single CBOR item; trailing bytes are an error."""
    reader = _Reader(bytes(data))
    value = _decode_item(reader, 0)
    if reader.offset != len(reader.data):
        raise CborError(f"{len(reader.data) - reader.offset} trailing bytes "
                        "after the CBOR item")
    return value
