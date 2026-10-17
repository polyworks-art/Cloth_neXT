# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict offline verification of the PROPOSED PolyWorks compact JWS contract.

No issuer keys are shipped until the backend contract/distribution is approved.
Use an audited cryptographic provider; missing providers fail closed.
"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass


class EntitlementError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EntitlementError('Duplicate JSON member')
        result[key] = value
    return result


def decode(segment):
    if not isinstance(segment, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', segment):
        raise EntitlementError('Invalid base64url segment')
    raw = base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))
    if base64.urlsafe_b64encode(raw).decode().rstrip('=') != segment:
        raise EntitlementError('Noncanonical base64url segment')
    return raw


@dataclass(frozen=True)
class Entitlement:
    subject: str
    tier: str
    installation_id: str
    expires_at: int
    license_id: str


def verify(token: str, *, keys: dict[str, bytes], issuer: str,
           installation_id: str, product: str = 'cloth-next', now=None) -> Entitlement:
    """Verify signature before trusting any claim. Keys come only from release code."""
    try:
        if not isinstance(token, str) or len(token) > 65536:
            raise EntitlementError('Invalid entitlement size')
        header64, payload64, signature64 = token.split('.')
        header = json.loads(decode(header64), object_pairs_hook=_pairs)
        if (not isinstance(header, dict) or set(header) != {'alg', 'typ', 'kid'}
                or header['alg'] != 'EdDSA' or header['typ'] != 'JWT'
                or not isinstance(header['kid'], str) or header['kid'] not in keys):
            raise EntitlementError('Untrusted entitlement header')
        key = keys[header['kid']]
        signature = decode(signature64)
        if len(key) != 32 or len(signature) != 64:
            raise EntitlementError('Invalid key or signature length')
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.exceptions import InvalidSignature
            from cryptography.exceptions import UnsupportedAlgorithm
        except ImportError as exc:
            raise EntitlementError('Ed25519 verification is unavailable in this runtime') from exc
        try:
            Ed25519PublicKey.from_public_bytes(key).verify(signature, f'{header64}.{payload64}'.encode('ascii'))
        except InvalidSignature as exc:
            raise EntitlementError('Invalid entitlement signature') from exc
        except UnsupportedAlgorithm as exc:
            raise EntitlementError('Ed25519 verification is unavailable in this runtime') from exc
        claims = json.loads(decode(payload64), object_pairs_hook=_pairs)
        if not isinstance(claims, dict):
            raise EntitlementError('Invalid claims')
        if (claims.get('iss') != issuer or claims.get('aud') != product
                or claims.get('product') != product or claims.get('installation_id') != installation_id):
            raise EntitlementError('Entitlement issuer, product or installation mismatch')
        if claims.get('tier') not in ('Personal', 'Commercial'):
            raise EntitlementError('Unknown license tier')
        for key in ('sub', 'jti'):
            if not isinstance(claims.get(key), str) or not claims[key].strip():
                raise EntitlementError('Missing entitlement identity')
        for key in ('iat', 'nbf', 'exp'):
            if type(claims.get(key)) is not int:
                raise EntitlementError('Invalid validity period')
        timestamp = time.time() if now is None else now
        if not (claims['iat'] <= timestamp and claims['nbf'] <= timestamp < claims['exp']
                and claims['exp'] > max(claims['iat'], claims['nbf'])):
            raise EntitlementError('Entitlement expired or not yet valid')
        return Entitlement(claims['sub'], claims['tier'], installation_id, claims['exp'], claims['jti'])
    except EntitlementError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise EntitlementError('Malformed entitlement') from exc
