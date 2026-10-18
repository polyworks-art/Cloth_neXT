# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated in-process backend implementing the frozen add-on contract v1.

Test double only: it follows docs/polyworks/BLENDER_ADDON_API_CONTRACT.md
(strict request bodies, error shape, device link, licences, activations,
signed entitlements, check-in). It is NOT the real backend; run
tools/polyworks_e2e.py against a real isolated backend for that.
Administrative hooks (approve, suspend, revoke, end sessions, go offline)
replace the portal and admin UI.
"""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class ContractBackend:
    DEVICE_LIMIT = 3

    def __init__(self, *, key_id="test-key-1", entitlement_seconds=30 * 24 * 3600):
        self.lock = threading.Lock()
        self.private_key = Ed25519PrivateKey.generate()
        self.key_id = key_id
        self.entitlement_seconds = entitlement_seconds
        self.clock = time.time
        self.users = {}       # id -> {email, password, mfa, verified}
        self.sessions = {}    # token -> {user, kind, ended}
        self.device_codes = {}
        self.mfa_tokens = {}
        self.licenses = {}    # id -> {..., seats_holders: {user: seat_id}}
        self.activations = {}  # id -> {license, user, installation_id, label, active}
        self.redeem_codes = {}
        self.requests = []    # (method, path, body, auth?) for assertions
        self.offline = False
        self.edge_503 = False
        self.rate_limit_next = 0
        self.malformed_next = 0
        self.server = None

    # Public key -----------------------------------------------------------------
    @property
    def public_key_pem(self) -> str:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def trust_pems(self) -> dict:
        return {self.key_id: self.public_key_pem}

    def sign(self, payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return b64url(raw) + "." + b64url(self.private_key.sign(raw))

    # Fixtures --------------------------------------------------------------------
    def add_user(self, email, password="correct horse", *, mfa=None, verified=True):
        user_id = str(uuid.uuid4())
        self.users[user_id] = {"id": user_id, "email": email, "password": password,
                               "mfa": mfa, "verified": verified}
        return user_id

    def add_license(self, user_id, *, tier="commercial", seats=1, state="active",
                    purchase_status="valid"):
        license_id = str(uuid.uuid4())
        self.licenses[license_id] = {"id": license_id, "product_id": "cloth-next", "tier": tier,
                                     "seats": seats, "purchase_provider": "superhive",
                                     "state": state, "purchase_status": purchase_status,
                                     "created_at": "2026-10-01T00:00:00Z", "owner": user_id,
                                     "holders": {user_id: str(uuid.uuid4())}}
        return license_id

    def add_redeem_code(self, user_hint=None, **license_kwargs):
        code = "CODE-" + secrets.token_hex(4).upper()
        self.redeem_codes[code] = license_kwargs
        return code

    def approve(self, user_code, user_id):
        for record in self.device_codes.values():
            if record["user_code"] == user_code:
                record["decision"] = ("approved", user_id)

    def deny(self, user_code):
        for record in self.device_codes.values():
            if record["user_code"] == user_code:
                record["decision"] = ("denied", None)

    def end_sessions(self, user_id):
        for session in self.sessions.values():
            if session["user"] == user_id:
                session["ended"] = True

    def set_license_state(self, license_id, state=None, purchase_status=None):
        if state:
            self.licenses[license_id]["state"] = state
        if purchase_status:
            self.licenses[license_id]["purchase_status"] = purchase_status

    # HTTP --------------------------------------------------------------------------
    def start(self):
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _dispatch(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                auth = self.headers.get("Authorization", "")
                status, body, headers = backend.handle(method, self.path, raw, auth)
                payload = b"" if body is None else (body if isinstance(body, bytes)
                                                    else json.dumps(body).encode())
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                if payload:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def do_DELETE(self):
                self._dispatch("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    @staticmethod
    def error(status, code, retry_after=None):
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        return status, {"error": {"code": code, "message": "server text must not be shown",
                                  "request_id": "req-" + code, "details": []}}, headers

    def _body(self, raw, fields):
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return None, self.error(422, "validation_error")
        if not isinstance(body, dict) or set(body) != set(fields):
            return None, self.error(422, "validation_error")
        return body, None

    def _user(self, auth):
        if not auth.startswith("Bearer "):
            return None
        session = self.sessions.get(auth[7:])
        if not session or session["ended"]:
            return None
        return session["user"]

    def _new_session(self, user_id):
        token = "tok_" + secrets.token_urlsafe(24)
        self.sessions[token] = {"user": user_id, "kind": "addon", "ended": False}
        return {"status": "authenticated", "token": token, "expires_at": "2027-01-15T00:00:00Z"}

    def _license_out(self, license_id, user_id):
        record = self.licenses[license_id]
        active = sum(1 for a in self.activations.values()
                     if a["license"] == license_id and a["user"] == user_id and a["active"])
        usable = record["state"] == "active" and record["purchase_status"] == "valid"
        return {**{k: record[k] for k in ("id", "product_id", "tier", "seats", "purchase_provider",
                                          "state", "created_at", "purchase_status")},
                "role": "owner" if record["owner"] == user_id else "member",
                "seat_id": record["holders"][user_id], "devices_active": active,
                "device_limit": self.DEVICE_LIMIT, "usable": usable,
                "future_field": "ignored by clients"}

    def _entitlement(self, activation_id):
        activation = self.activations[activation_id]
        record = self.licenses[activation["license"]]
        now = int(self.clock())
        return self.sign({"typ": "polyworks.entitlement", "v": 1, "kid": self.key_id,
                          "lic": record["id"], "sub": activation["user"], "act": activation_id,
                          "inst": activation["installation_id"], "prd": "cloth-next",
                          "tier": record["tier"], "seats": record["seats"],
                          "seat": record["holders"][activation["user"]],
                          "iat": now, "exp": now + self.entitlement_seconds})

    def _activation_out(self, activation_id):
        a = self.activations[activation_id]
        return {"id": activation_id, "installation_id": a["installation_id"], "label": a["label"],
                "activated_at": a["activated_at"], "last_seen_at": a["activated_at"]}

    def handle(self, method, path, raw, auth):
        with self.lock:
            self.requests.append((method, path, raw, bool(auth)))
            if self.offline:
                return 502, b"bad gateway", None
            if self.edge_503:
                return 503, {"error": {"code": "service_unavailable", "message": "edge"}}, None
            if self.rate_limit_next:
                self.rate_limit_next -= 1
                return self.error(429, "rate_limited", retry_after=7)
            if self.malformed_next:
                self.malformed_next -= 1
                return 200, b"{not json", None
            return self._route(method, path, raw, auth)

    def _route(self, method, path, raw, auth):
        if method == "POST" and path == "/v1/auth/device/start":
            body, error = self._body(raw, {"client_name"})
            if error:
                return error
            code = secrets.token_urlsafe(32)
            user_code = "BCDF-" + secrets.token_hex(2).upper()
            self.device_codes[code] = {"user_code": user_code, "decision": None,
                                       "expires": self.clock() + 600, "collected": False,
                                       "last_poll": 0}
            return 200, {"device_code": code, "user_code": user_code,
                         "verification_uri": f"{self.url}/link",
                         "verification_uri_complete": f"{self.url}/link?code={user_code}",
                         "expires_in": 600, "interval": 1}, None
        if method == "POST" and path == "/v1/auth/device/token":
            body, error = self._body(raw, {"device_code"})
            if error:
                return error
            record = self.device_codes.get(body["device_code"])
            if not record or record["collected"]:
                return self.error(400, "invalid_device_code")
            if self.clock() > record["expires"]:
                return self.error(400, "expired_token")
            if record["decision"] is None:
                return self.error(400, "authorization_pending")
            if record["decision"][0] == "denied":
                return self.error(400, "access_denied")
            record["collected"] = True
            return 200, self._new_session(record["decision"][1]), None
        if method == "POST" and path == "/v1/auth/login":
            body, error = self._body(raw, {"email", "password", "token_mode"})
            if error:
                return error
            user = next((u for u in self.users.values() if u["email"] == body["email"]), None)
            if not user or user["password"] != body["password"]:
                return self.error(401, "invalid_credentials")
            if user["mfa"]:
                token = secrets.token_urlsafe(16)
                self.mfa_tokens[token] = {"user": user["id"], "attempts": 5}
                return 200, {"status": "mfa_required", "mfa_token": token}, None
            return 200, self._new_session(user["id"]), None
        if method == "POST" and path == "/v1/auth/login/mfa":
            body, error = self._body(raw, {"mfa_token", "code", "token_mode"})
            if error:
                return error
            record = self.mfa_tokens.get(body["mfa_token"])
            if not record or record["attempts"] <= 0:
                return self.error(401, "invalid_credentials")
            if self.users[record["user"]]["mfa"] != body["code"]:
                record["attempts"] -= 1
                return self.error(401, "invalid_credentials")
            del self.mfa_tokens[body["mfa_token"]]
            return 200, self._new_session(record["user"]), None
        if method == "GET" and path == "/v1/entitlements/public-key":
            return 200, {"key_id": self.key_id, "public_key_pem": self.public_key_pem}, None

        user_id = self._user(auth)
        if user_id is None:
            return self.error(401, "not_authenticated")
        if method == "GET" and path == "/v1/auth/me":
            user = self.users[user_id]
            return 200, {"id": user_id, "email": user["email"], "email_verified": user["verified"],
                         "created_at": "2026-10-01T00:00:00Z"}, None
        if method == "POST" and path == "/v1/auth/logout":
            self.sessions[auth[7:]]["ended"] = True
            return 204, None, None
        if method == "GET" and path == "/v1/licenses":
            return 200, [self._license_out(i, user_id) for i, r in self.licenses.items()
                         if user_id in r["holders"]], None
        if method == "POST" and path == "/v1/licenses/redeem":
            body, error = self._body(raw, {"code"})
            if error:
                return error
            if not self.users[user_id]["verified"]:
                return self.error(403, "forbidden")
            kwargs = self.redeem_codes.pop(body["code"], None)
            if kwargs is None:
                return self.error(404, "not_found")
            return 201, self._license_out(self.add_license(user_id, **kwargs), user_id), None
        match = re.fullmatch(r"/v1/licenses/([^/]+)/activations(?:/([^/]+))?", path)
        if match:
            license_id, activation_id = match.groups()
            record = self.licenses.get(license_id)
            if record is None:
                return self.error(404, "not_found")
            if user_id not in record["holders"]:
                return self.error(403, "forbidden")
            mine = {i: a for i, a in self.activations.items()
                    if a["license"] == license_id and a["user"] == user_id and a["active"]}
            if method == "POST" and activation_id is None:
                body, error = self._body(raw, {"installation_id", "label"})
                if error:
                    return error
                if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", body["installation_id"]):
                    return self.error(422, "validation_error")
                if record["state"] != "active" or record["purchase_status"] != "valid":
                    return self.error(409, "license_inactive")
                existing = next((i for i, a in mine.items()
                                 if a["installation_id"] == body["installation_id"]), None)
                if existing is None:
                    if len(mine) >= self.DEVICE_LIMIT:
                        return self.error(409, "device_limit")
                    existing = str(uuid.uuid4())
                    self.activations[existing] = {"license": license_id, "user": user_id,
                                                  "installation_id": body["installation_id"],
                                                  "label": body["label"], "active": True,
                                                  "activated_at": "2026-10-17T12:00:00Z"}
                return 200, {"activation": self._activation_out(existing),
                             "entitlement": self._entitlement(existing)}, None
            if method == "GET" and activation_id is None:
                return 200, [self._activation_out(i) for i in mine], None
            if method == "DELETE" and activation_id:
                if activation_id not in mine:
                    return self.error(404, "not_found")
                self.activations[activation_id]["active"] = False
                return 204, None, None
        if method == "POST" and path == "/v1/entitlements/check-in":
            body, error = self._body(raw, {"entitlement", "installation_id"})
            if error:
                return error
            return 200, self._check_in(user_id, body), {"Cache-Control": "no-store"}
        return self.error(404, "not_found")

    def _check_in(self, user_id, body):
        token = body["entitlement"]
        try:
            payload_part, signature_part = token.split(".", 1)
            raw = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
            signature = base64.urlsafe_b64decode(signature_part + "=" * (-len(signature_part) % 4))
            self.private_key.public_key().verify(signature, raw)
            payload = json.loads(raw)
        except Exception:
            return {"status": "invalid", "reason": None, "entitlement": None, "license": None}
        if payload["sub"] != user_id or payload["inst"] != body["installation_id"]:
            return {"status": "invalid", "reason": None, "entitlement": None, "license": None}
        record = self.licenses.get(payload["lic"])
        activation = self.activations.get(payload["act"])
        reason = None
        if record is None or record["state"] == "revoked":
            reason = "license_revoked"
        elif record["purchase_status"] == "refunded":
            reason = "refunded"
        elif record["state"] == "suspended":
            reason = "license_suspended"
        elif user_id not in record["holders"]:
            reason = "seat_released"
        elif activation is None or not activation["active"]:
            reason = "device_deactivated"
        if reason:
            return {"status": "revoked", "reason": reason, "entitlement": None, "license": None}
        return {"status": "valid", "reason": None, "entitlement": self._entitlement(payload["act"]),
                "license": self._license_out(record["id"], user_id)}
