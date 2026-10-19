# SPDX-License-Identifier: GPL-3.0-or-later
"""Ed25519 signature verification (RFC 8032, section 5.1.7).

Blender's bundled Python has no ``cryptography`` package, so the add-on needs a
dependency-free verifier. ``verify`` prefers ``cryptography`` when it is
importable (the Companion build ships it) and otherwise uses the reference
algorithm below. Only public data is processed: there is no signing and no
secret key, so constant-time arithmetic is not required.
"""
from __future__ import annotations

import base64
import binascii
import hashlib

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)
# DER prefix of an Ed25519 SubjectPublicKeyInfo (RFC 8410): the raw key follows.
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


class KeyFormatError(ValueError):
    pass


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _multiply(scalar: int, point):
    result = (0, 1, 1, 0)
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a, b) -> bool:
    return ((a[0] * b[2] - b[0] * a[2]) % _P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _P == 0)


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_BASE = (_GX, _GY, 1, _GX * _GY % _P)


def _verify_reference(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(),
                       "little") % _L
    return _equal(_multiply(s, _BASE), _add(r, _multiply(h, a)))


def _verify_cryptography(public_key: bytes, message: bytes, signature: bytes):
    """True/False, or None when the provider is unavailable."""
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return None
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except InvalidSignature:
        return False
    except (UnsupportedAlgorithm, ValueError):
        return None


def verify(public_key: bytes, message: bytes, signature: bytes, *,
           use_provider: bool = True) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    if use_provider:
        result = _verify_cryptography(public_key, message, signature)
        if result is not None:
            return result
    return _verify_reference(public_key, message, signature)


def raw_public_key_from_pem(pem: str) -> bytes:
    """Return the 32-byte key of an Ed25519 ``PUBLIC KEY`` PEM block."""
    if not isinstance(pem, str):
        raise KeyFormatError("public key must be text")
    lines = [line.strip() for line in pem.strip().splitlines() if line.strip()]
    if (len(lines) < 3 or lines[0] != "-----BEGIN PUBLIC KEY-----"
            or lines[-1] != "-----END PUBLIC KEY-----"):
        raise KeyFormatError("not a PUBLIC KEY PEM block")
    try:
        der = base64.b64decode("".join(lines[1:-1]), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KeyFormatError("invalid PEM base64") from exc
    if len(der) != len(_SPKI_PREFIX) + 32 or not der.startswith(_SPKI_PREFIX):
        raise KeyFormatError("not an Ed25519 public key")
    return der[len(_SPKI_PREFIX):]
