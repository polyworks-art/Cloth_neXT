# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP client for the frozen PolyWorks add-on contract v1. Companion only.

Blender never imports this module (enforced by tests). Request bodies contain
exactly the documented fields (the server rejects unknown ones); unknown
response fields are ignored. Errors branch on ``error.code`` only.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import json
import os
import ssl
from urllib.parse import quote, urlsplit

PRODUCTION_API = "https://api.cloth-next.de"
PRODUCTION_PORTAL = "https://account.cloth-next.de"
API_ENV = "CLOTH_NEXT_POLYWORKS_API_URL"
PORTAL_ENV = "CLOTH_NEXT_POLYWORKS_PORTAL_URL"
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0
MAX_RESPONSE_BYTES = 1024 * 1024
# Codes the client treats as "offline": the stored entitlement stays valid.
OFFLINE_CODES = frozenset({"network_unavailable", "service_unavailable", "server_error",
                           "feature_disabled", "internal_error"})


class ApiError(Exception):
    """``code`` is the contract error code, or a client code:
    network_unavailable, service_unavailable, server_error, invalid_response."""

    def __init__(self, code: str, status: int = 0, *, request_id: str = "",
                 retry_after: float | None = None, details=None):
        self.code = code
        self.status = status
        self.request_id = request_id
        self.retry_after = retry_after
        self.details = details
        super().__init__(f"{code} (HTTP {status})" if status else code)

    @property
    def offline(self) -> bool:
        return self.code in OFFLINE_CODES

    @property
    def session_dead(self) -> bool:
        return self.status == 401


def _is_loopback(host: str) -> bool:
    # IP literals only: "localhost" is resolved through the hosts file and DNS.
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_origin(url: str) -> str:
    """HTTPS origin, or plain HTTP only for a loopback test backend."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if (not host or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise ValueError(f"not a bare origin: {url!r}")
    if parts.scheme == "https" or (parts.scheme == "http" and _is_loopback(host)):
        return f"{parts.scheme}://{parts.netloc}"
    raise ValueError("the PolyWorks API must use HTTPS outside a loopback test backend")


@dataclass(frozen=True)
class Endpoints:
    api: str = PRODUCTION_API
    portal: str = PRODUCTION_PORTAL

    @property
    def is_production(self) -> bool:
        return self.api == PRODUCTION_API

    @classmethod
    def from_environment(cls, environ=None, api: str | None = None, portal: str | None = None):
        environ = os.environ if environ is None else environ
        api_url = validate_origin(api or environ.get(API_ENV) or PRODUCTION_API)
        portal_url = validate_origin(portal or environ.get(PORTAL_ENV) or (
            PRODUCTION_PORTAL if api_url == PRODUCTION_API else api_url))
        return cls(api_url, portal_url)

    def browser_url(self, url: str) -> str:
        """Accept a server-supplied URL only on the portal or API origin."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}" if parts.scheme else ""
        if (origin not in (self.portal, self.api) or parts.username or parts.password
                or any(c in url for c in "\r\n\t ")):
            raise ApiError("invalid_response")
        return url


class HttpTransport:
    """One request per connection, no redirects, bounded response size."""

    def __init__(self, endpoints: Endpoints, *, context: ssl.SSLContext | None = None):
        self.endpoints = endpoints
        self.context = context

    def request(self, method: str, path: str, body: dict | None = None, token: str | None = None):
        parts = urlsplit(self.endpoints.api)
        headers = {"Accept": "application/json", "User-Agent": "ClothNeXt-Companion"}
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            if any(c in token for c in "\r\n"):
                raise ApiError("invalid_response")
            headers["Authorization"] = "Bearer " + token
        if parts.scheme == "https":
            connection = http.client.HTTPSConnection(
                parts.hostname, parts.port, timeout=CONNECT_TIMEOUT,
                context=self.context or ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection(parts.hostname, parts.port,
                                                    timeout=CONNECT_TIMEOUT)
        try:
            connection.connect()
            connection.sock.settimeout(READ_TIMEOUT)
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            status = response.status
            retry_after = response.getheader("Retry-After")
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            raise ApiError("network_unavailable") from exc
        finally:
            connection.close()
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ApiError("invalid_response", status)
        return status, raw, retry_after


def _decode_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        return None


def _retry_after(value) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= 24 * 3600 else None


def _error(status: int, raw: bytes, retry_after) -> ApiError:
    body = _decode_json(raw)
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    request_id = error.get("request_id", "") if isinstance(error, dict) else ""
    details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(code, str) or not code:
        # No contract error body: an edge proxy, or a non-contract server.
        code = ("service_unavailable" if status == 503 else
                "server_error" if status >= 500 else
                "not_authenticated" if status == 401 else "invalid_response")
    elif status >= 500 and code not in ("feature_disabled", "internal_error"):
        code = "service_unavailable" if status == 503 else "server_error"
    return ApiError(code, status, request_id=request_id if isinstance(request_id, str) else "",
                    retry_after=_retry_after(retry_after), details=details)


def _field(value: dict, name: str, kind, *, optional=False):
    item = value.get(name) if isinstance(value, dict) else None
    if item is None and optional:
        return None
    if kind is int:
        if type(item) is not int:
            raise ApiError("invalid_response")
    elif not isinstance(item, kind):
        raise ApiError("invalid_response")
    return item


@dataclass(frozen=True)
class DeviceStart:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int

    def __repr__(self):  # never print the secret device code
        return f"DeviceStart(user_code={self.user_code!r}, expires_in={self.expires_in})"


@dataclass(frozen=True)
class Session:
    token: str
    expires_at: str

    def __repr__(self):
        return f"Session(expires_at={self.expires_at!r})"


@dataclass(frozen=True)
class MfaRequired:
    mfa_token: str

    def __repr__(self):
        return "MfaRequired()"


def license_from(value) -> dict:
    """Contract 3.1 LicenseOut, reduced to the documented fields."""
    if not isinstance(value, dict):
        raise ApiError("invalid_response")
    result = {"id": _field(value, "id", str), "usable": _field(value, "usable", bool)}
    for name in ("product_id", "tier", "purchase_provider", "state", "created_at",
                 "purchase_status", "role", "seat_id"):
        item = value.get(name)
        result[name] = item if isinstance(item, str) else None
    for name in ("seats", "devices_active", "device_limit"):
        item = value.get(name)
        result[name] = item if type(item) is int else None
    return result


def activation_from(value) -> dict:
    if not isinstance(value, dict):
        raise ApiError("invalid_response")
    result = {"id": _field(value, "id", str),
              "installation_id": _field(value, "installation_id", str)}
    for name in ("label", "activated_at", "last_seen_at"):
        item = value.get(name)
        result[name] = item if isinstance(item, str) else None
    return result


class PolyWorksApi:
    def __init__(self, endpoints: Endpoints, transport=None):
        self.endpoints = endpoints
        self.transport = transport or HttpTransport(endpoints)

    def _call(self, method, path, body=None, token=None, *, expect=(200,)):
        status, raw, retry_after = self.transport.request(method, path, body, token)
        if status not in expect:
            raise _error(status, raw, retry_after)
        if status == 204:
            return None
        value = _decode_json(raw)
        if value is None:
            raise ApiError("invalid_response", status)
        return value

    # 2.1 Device link ---------------------------------------------------------
    def device_start(self, client_name: str) -> DeviceStart:
        value = self._call("POST", "/v1/auth/device/start", {"client_name": client_name})
        start = DeviceStart(
            _field(value, "device_code", str), _field(value, "user_code", str),
            self.endpoints.browser_url(_field(value, "verification_uri", str)),
            self.endpoints.browser_url(_field(value, "verification_uri_complete", str)),
            _field(value, "expires_in", int), _field(value, "interval", int))
        if not (start.device_code and start.user_code and 0 < start.expires_in <= 3600
                and 1 <= start.interval <= 120):
            raise ApiError("invalid_response")
        return start

    def device_token(self, device_code: str) -> Session:
        return self._session(self._call("POST", "/v1/auth/device/token",
                                        {"device_code": device_code}))

    # 2.2 Password login --------------------------------------------------------
    def login(self, email: str, password: str):
        value = self._call("POST", "/v1/auth/login",
                           {"email": email, "password": password, "token_mode": "bearer"})
        if isinstance(value, dict) and value.get("status") == "mfa_required":
            return MfaRequired(_field(value, "mfa_token", str))
        return self._session(value)

    def login_mfa(self, mfa_token: str, code: str) -> Session:
        return self._session(self._call("POST", "/v1/auth/login/mfa",
                                        {"mfa_token": mfa_token, "code": code,
                                         "token_mode": "bearer"}))

    @staticmethod
    def _session(value) -> Session:
        if not isinstance(value, dict) or value.get("status") != "authenticated":
            raise ApiError("invalid_response")
        token = _field(value, "token", str)
        if not token or any(c in token for c in "\r\n "):
            raise ApiError("invalid_response")
        return Session(token, _field(value, "expires_at", str, optional=True) or "")

    # 2.3 Session -------------------------------------------------------------------
    def me(self, token: str) -> dict:
        value = self._call("GET", "/v1/auth/me", token=token)
        if not isinstance(value, dict):
            raise ApiError("invalid_response")
        return {"id": _field(value, "id", str), "email": _field(value, "email", str),
                "email_verified": value.get("email_verified")
                if isinstance(value.get("email_verified"), bool) else None}

    def logout(self, token: str) -> None:
        self._call("POST", "/v1/auth/logout", token=token, expect=(204, 200))

    # 3 Licences ----------------------------------------------------------------------
    def licenses(self, token: str) -> list:
        value = self._call("GET", "/v1/licenses", token=token)
        if not isinstance(value, list):
            raise ApiError("invalid_response")
        return [license_from(item) for item in value]

    def redeem(self, token: str, code: str) -> dict:
        return license_from(self._call("POST", "/v1/licenses/redeem", {"code": code},
                                       token=token, expect=(201, 200)))

    def activate(self, token: str, license_id: str, installation_id: str, label: str):
        value = self._call("POST", f"/v1/licenses/{quote(license_id, safe='')}/activations",
                           {"installation_id": installation_id, "label": label}, token=token)
        if not isinstance(value, dict):
            raise ApiError("invalid_response")
        return activation_from(value.get("activation")), _field(value, "entitlement", str)

    def activations(self, token: str, license_id: str) -> list:
        value = self._call("GET", f"/v1/licenses/{quote(license_id, safe='')}/activations",
                           token=token)
        if not isinstance(value, list):
            raise ApiError("invalid_response")
        return [activation_from(item) for item in value]

    def deactivate(self, token: str, license_id: str, activation_id: str) -> None:
        self._call("DELETE", f"/v1/licenses/{quote(license_id, safe='')}/activations/"
                   f"{quote(activation_id, safe='')}", token=token, expect=(204, 200))

    # 4 Entitlements ---------------------------------------------------------------
    def check_in(self, token: str, entitlement: str, installation_id: str) -> dict:
        value = self._call("POST", "/v1/entitlements/check-in",
                           {"entitlement": entitlement, "installation_id": installation_id},
                           token=token)
        status = _field(value, "status", str)
        reason = value.get("reason") if isinstance(value.get("reason"), str) else None
        result = {"status": status, "reason": reason, "entitlement": None, "license": None}
        if status == "valid":
            result["entitlement"] = _field(value, "entitlement", str)
            if isinstance(value.get("license"), dict):
                result["license"] = license_from(value["license"])
        elif status not in ("revoked", "invalid"):
            # Contract 8: an unknown status is never treated as valid.
            result["status"] = "revoked"
        return result

    # Companion v1 GAIA routes ------------------------------------------------
    def gaia_access(self, token: str) -> dict:
        return _gaia_access(self._call("GET", "/v1/account/gaia-access", token=token))

    def request_gaia_access(self, token: str, message: str = "") -> str:
        body = {"message": message[:1000]} if message.strip() else {}
        value = self._call("POST", "/v1/account/gaia-access/request", body, token=token)
        return _gaia_access(value)["status"] or "pending"

    def redeem_gaia_invite(self, token: str, invite: str) -> dict:
        value = self._call("POST", "/v1/account/gaia-invite/redeem", {"token": invite}, token=token)
        access = value.get("access") if isinstance(value, dict) else None
        return {"access": access is True, "mode": _gaia_access(value)["mode"]}

    def gaia_entitlement(self, token: str, installation_id: str) -> dict:
        """Installation-bound GAIA feature entitlement (TETHYS server contract).

        {"status": granted|pending|declined|None, "entitlement": token|None}.
        Only the server's GAIA access decision grants it; no licence needed.
        """
        value = self._call("POST", "/v1/account/gaia-entitlement",
                           {"installation_id": installation_id}, token=token)
        entitlement = value.get("entitlement") if isinstance(value, dict) else None
        status = _gaia_access(value)["status"]
        if status == "granted" and not (isinstance(entitlement, str) and entitlement):
            raise ApiError("invalid_response")
        return {"status": status, "entitlement": entitlement if status == "granted" else None}

    def account_downloads(self, token: str) -> dict:
        value = self._call("GET", "/v1/account/downloads", token=token)
        builds = value.get("builds") if isinstance(value, dict) else None
        if not isinstance(builds, list):
            raise ApiError("invalid_response")
        return {"enabled": value.get("enabled") is True, "access": value.get("access") is True,
                "builds": [gaia_build_from(item) for item in builds]}

    def download_session(self, token: str, build_id: str) -> dict:
        from urllib.parse import quote
        value = self._call("POST", f"/v1/solver/builds/{quote(build_id, safe='')}/download-session",
                           token=token)
        digest = _field(value, "sha256", str).lower()
        size = _field(value, "size_bytes", int)
        if len(digest) != 64 or not set(digest) <= _HEX64 or size <= 0:
            raise ApiError("invalid_response")
        return {"url": _field(value, "url", str), "sha256": digest, "size_bytes": size,
                "expires_at": _field(value, "expires_at", str, optional=True)}

    def public_key(self) -> dict:
        value = self._call("GET", "/v1/entitlements/public-key")
        return {"key_id": _field(value, "key_id", str),
                "public_key_pem": _field(value, "public_key_pem", str)}



# Companion v1 GAIA routes (docs/polyworks/COMPANION_DOWNLOAD_API_HANDOFF.md) -------
GAIA_ACCESS_STATES = frozenset({"granted", "pending", "declined"})
GAIA_MODES = frozenset({"none", "selected", "everyone"})
_HEX64 = frozenset("0123456789abcdef")


def gaia_build_from(value) -> dict:
    """One published GAIA build; unknown fields are ignored, wrong types rejected."""
    if not isinstance(value, dict):
        raise ApiError("invalid_response")
    digest = _field(value, "sha256", str).lower()
    size = _field(value, "size_bytes", int)
    if len(digest) != 64 or not set(digest) <= _HEX64 or size <= 0:
        raise ApiError("invalid_response")
    result = {"build_id": _field(value, "build_id", str), "version": _field(value, "version", str),
              "sha256": digest, "size_bytes": size}
    for name in ("channel", "platform", "backend", "min_addon_version", "max_addon_version",
                 "release_notes", "published_at"):
        item = value.get(name)
        result[name] = item if isinstance(item, str) else None
    return result


def _gaia_access(value) -> dict:
    status = value.get("status") if isinstance(value, dict) else None
    mode = value.get("mode") if isinstance(value, dict) else None
    # Handoff 4: unknown values are treated as "no access".
    return {"status": status if status in GAIA_ACCESS_STATES else None,
            "mode": mode if mode in GAIA_MODES else "none"}
