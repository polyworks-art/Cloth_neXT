# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Send one diagnostics package.

With a signed-in PolyWorks account the session token goes in the
``Authorization`` header so the server can link the package to the account;
without one it carries only the hashed ``hardware_id``. The token is
never written into the package, a file or a log.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from .payload import PATH

PRODUCTION_API = "https://api.cloth-next.de"
API_ENV = "CLOTH_NEXT_POLYWORKS_API_URL"
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 15.0
MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class Outcome:
    """``sent``: drop the events. ``drop``: the server refused this package for good.
    ``retry``: keep the events and try again after ``retry_after`` seconds (None = backoff)."""
    kind: str
    status: int = 0
    retry_after: float | None = None


def api_origin(environ=None) -> str:
    """HTTPS origin, or plain HTTP only for a loopback test backend."""
    env = os.environ if environ is None else environ
    url = env.get(API_ENV) or PRODUCTION_API
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if (not host or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise ValueError("not a bare origin")
    if parts.scheme == "https":
        return f"https://{parts.netloc}"
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parts.scheme == "http" and loopback:
        return f"http://{parts.netloc}"
    raise ValueError("diagnostics must use HTTPS outside a loopback test backend")


def _retry_after(value) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def classify(status: int, retry_after=None) -> Outcome:
    if status in (200, 201, 202):
        return Outcome("sent", status)
    if status in (400, 413, 422):
        return Outcome("drop", status)
    if status == 429:
        return Outcome("retry", status, _retry_after(retry_after))
    # 401/403/404: the endpoint is not open (yet); 5xx: outage. Keep the events.
    return Outcome("retry", status, _retry_after(retry_after))


def post_package(origin: str, package: dict, *, token: str | None = None,
                 user_agent: str = "ClothNeXt-Diagnostics",
                 context: ssl.SSLContext | None = None) -> Outcome:
    parts = urlsplit(origin)
    body = json.dumps(package, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": user_agent}
    if token and not any(c in token for c in "\r\n"):
        headers["Authorization"] = "Bearer " + token
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=CONNECT_TIMEOUT,
                                                 context=context or ssl.create_default_context())
    else:
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=CONNECT_TIMEOUT)
    try:
        connection.connect()
        connection.sock.settimeout(READ_TIMEOUT)
        connection.request("POST", PATH, body=body, headers=headers)
        response = connection.getresponse()
        response.read(MAX_RESPONSE_BYTES)
        return classify(response.status, response.getheader("Retry-After"))
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return Outcome("retry")
    finally:
        connection.close()
