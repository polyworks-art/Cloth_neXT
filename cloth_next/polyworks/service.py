# SPDX-License-Identifier: GPL-3.0-or-later
"""Companion account and licence workflows (contract section 9). Companion only.

Every operation runs on a Companion worker thread under ``operation()``, the
cross-process lock shared by all Companion instances. Results are published to
``state.json`` and the ``entitlement`` file; Blender only reads those files.
Bearer tokens live in the vault and in local variables, never in state, logs,
exceptions or reprs.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import platform
import time

from . import messages
from .api import ApiError, Endpoints, PolyWorksApi
from .entitlements import EntitlementError, verify
from .state import sanitize, utc_now_iso, parse_iso
from .status import StatusView, compute_view
from .storage import PRESENCE_FILE, STATE_FILE, LocalStore, StorageError, exclusive_operation
from .vault import default_vault
from .trust import TrustStore, load_trust

LOG = logging.getLogger(__name__)
CHECK_IN_INTERVAL = 4 * 3600  # contract 4.4: at start-up, then at most every few hours
BACKOFF = (60, 300, 900, 3600)  # contract 7: 1, 5 and 15 minutes, then hourly
RATE_LIMIT_DEFAULT = 900


class ServiceError(Exception):
    def __init__(self, code: str, *, request_id: str = "", retry_after: float | None = None):
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after
        super().__init__(messages.companion_message(code, request_id))


@dataclass
class DeviceLink:
    """In-memory only; ``device_code`` is a secret and never persisted."""
    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_at: float
    interval: float
    next_poll: float


@dataclass
class MfaChallenge:
    mfa_token: str = field(repr=False)
    attempts_left: int = 5


def default_label() -> str:
    """Device label shown in the portal; editable before activation."""
    name = platform.node().split(".")[0][:40] or "Computer"
    system = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(platform.system(), "")
    return f"{name} ({system})" if system else name


class AccountService:
    def __init__(self, store: LocalStore | None = None, *, endpoints: Endpoints | None = None,
                 api: PolyWorksApi | None = None, vault=None, trust: TrustStore | None = None,
                 clock=time.time, monotonic=time.monotonic, client_name: str = "Cloth NeXt"):
        self.store = store or LocalStore()
        self.endpoints = endpoints or Endpoints.from_environment()
        self.api = api or PolyWorksApi(self.endpoints)
        self._vault = vault
        self.trust = trust or load_trust()
        self.clock = clock
        self.monotonic = monotonic
        self.client_name = client_name[:120]

    # Infrastructure --------------------------------------------------------------
    @property
    def vault(self):
        if self._vault is None:
            self._vault = default_vault(self.store.root)
        return self._vault

    @contextmanager
    def operation(self, *, blocking: bool = False):
        try:
            with exclusive_operation(self.store.root, blocking=blocking):
                yield
        except StorageError as exc:
            if "Another" in str(exc):
                raise ServiceError("busy") from exc
            raise

    def state(self) -> dict:
        return sanitize(self.store.read_json(STATE_FILE) or {})

    def _save(self, state: dict) -> dict:
        state["written_at"] = utc_now_iso(self.clock())
        state["account"]["storage"] = getattr(self.vault, "kind", None)
        clean = sanitize(state)
        self.store.write_json(STATE_FILE, clean)
        return clean

    def _token(self) -> str | None:
        try:
            session = self.vault.load()
        except StorageError as exc:
            raise ServiceError("storage_unavailable") from exc
        token = session.get("token") if isinstance(session, dict) else None
        return token if isinstance(token, str) and token else None

    def _require_token(self) -> str:
        token = self._token()
        if not token:
            raise ServiceError("sign_in_required")
        return token

    def installation_id(self) -> str:
        try:
            return self.store.installation_id()
        except (StorageError, OSError) as exc:
            raise ServiceError("storage_unavailable") from exc

    def view(self) -> StatusView:
        return compute_view(state=self.store.read_json(STATE_FILE),
                            token=self.store.read_entitlement(),
                            installation_id=self.store.read_installation_id(),
                            trust=self.trust, presence=self.store.read_json(PRESENCE_FILE, 4096),
                            now=self.clock())

    # Error bookkeeping ---------------------------------------------------------
    def _offline(self, state: dict, exc: ApiError) -> ServiceError:
        now = self.clock()
        connection = state["connection"]
        step = connection["retry_step"]
        connection.update(online=False, last_attempt_at=utc_now_iso(now), last_error=exc.code,
                          next_check_at=utc_now_iso(now + BACKOFF[min(step, len(BACKOFF) - 1)]),
                          retry_step=step + 1)
        if state["account"]["signed_in"] and state["account"]["session"] == "active":
            state["account"]["session"] = "unverified"
        self._save(state)
        return ServiceError(exc.code, request_id=exc.request_id)

    def _rate_limited(self, state: dict, exc: ApiError) -> ServiceError:
        now = self.clock()
        wait = exc.retry_after if exc.retry_after is not None else RATE_LIMIT_DEFAULT
        state["connection"].update(online=True, last_attempt_at=utc_now_iso(now),
                                   last_error="rate_limited", next_check_at=utc_now_iso(now + wait))
        self._save(state)
        return ServiceError("rate_limited", retry_after=wait)

    def _session_dead(self, state: dict) -> ServiceError:
        """Contract 2.3/4.5: discard the token, keep the offline entitlement."""
        try:
            self.vault.clear()
        except StorageError:
            LOG.warning("PolyWorks session could not be removed from local storage")
        state["account"].update(signed_in=False, session="expired", session_expires_at=None)
        self._save(state)
        return ServiceError("not_authenticated")

    def _handle(self, state: dict, exc: ApiError) -> ServiceError:
        if exc.offline:
            return self._offline(state, exc)
        if exc.code == "rate_limited":
            return self._rate_limited(state, exc)
        if exc.session_dead:
            return self._session_dead(state)
        return ServiceError(exc.code, request_id=exc.request_id, retry_after=exc.retry_after)

    def _online(self, state: dict) -> None:
        now = self.clock()
        state["connection"].update(online=True, last_success_at=utc_now_iso(now),
                                   last_attempt_at=utc_now_iso(now), last_error=None,
                                   next_check_at=utc_now_iso(now + CHECK_IN_INTERVAL), retry_step=0)

    # Sign-in -------------------------------------------------------------------------
    def start_device_link(self) -> DeviceLink:
        state = self.state()
        try:
            start = self.api.device_start(self.client_name)
        except ApiError as exc:
            raise self._handle(state, exc) from None
        now = self.monotonic()
        return DeviceLink(start.device_code, start.user_code, start.verification_uri,
                          start.verification_uri_complete, now + start.expires_in,
                          float(start.interval), now + start.interval)

    def poll_device_link(self, link: DeviceLink) -> bool:
        """True once signed in, False while pending; ServiceError when finished."""
        now = self.monotonic()
        if now >= link.expires_at:
            raise ServiceError("expired_token")
        if now < link.next_poll:
            return False
        link.next_poll = now + link.interval
        try:
            session = self.api.device_token(link.device_code)
        except ApiError as exc:
            if exc.code == "authorization_pending":
                return False
            if exc.code == "slow_down":
                link.interval += 5
                link.next_poll = now + link.interval
                return False
            if exc.code == "rate_limited":
                link.next_poll = now + (exc.retry_after or link.interval * 2)
                return False
            if exc.offline:
                return False  # keep polling until the link expires
            raise ServiceError(exc.code, request_id=exc.request_id) from None
        self._establish(session)
        return True

    def login_password(self, email: str, password: str):
        """Returns None when signed in, or an MfaChallenge. Never stores the password."""
        state = self.state()
        try:
            result = self.api.login(email.strip(), password)
        except ApiError as exc:
            if exc.code == "invalid_credentials":
                raise ServiceError("invalid_credentials") from None
            raise self._handle(state, exc) from None
        if hasattr(result, "mfa_token"):
            return MfaChallenge(result.mfa_token)
        self._establish(result)
        return None

    def login_mfa(self, challenge: MfaChallenge, code: str) -> None:
        state = self.state()
        try:
            session = self.api.login_mfa(challenge.mfa_token, code.strip().replace(" ", ""))
        except ApiError as exc:
            if exc.status in (400, 401, 422) and not exc.offline:
                challenge.attempts_left -= 1
                raise ServiceError("mfa_invalid") from None
            raise self._handle(state, exc) from None
        self._establish(session)

    def _establish(self, session) -> None:
        try:
            self.vault.save({"token": session.token, "expires_at": session.expires_at})
        except StorageError as exc:
            raise ServiceError("storage_unavailable") from exc
        state = self.state()
        state["account"].update(signed_in=True, session="active",
                                session_expires_at=session.expires_at or None)
        if not self.store.read_entitlement():
            state["check_in"].update(status=None, reason=None, at=None)
        self._save(state)
        self.refresh()

    # Status refresh and renewal ---------------------------------------------------
    def refresh(self, *, check_in: bool = True) -> StatusView:
        """Workflows 2, 6, 7: account, licences, then check-in of a stored entitlement."""
        state = self.state()
        token = self._token()
        if not token:
            state["account"].update(signed_in=False, session_expires_at=None,
                                    session="expired" if state["account"]["session"] == "expired" else "none")
            self._save(state)
            return self.view()
        try:
            account = self.api.me(token)
            licenses = self.api.licenses(token)
        except ApiError as exc:
            raise self._handle(state, exc) from None
        state["account"].update(signed_in=True, session="active", email=account["email"],
                                email_verified=account["email_verified"])
        state["licenses"] = licenses
        self._online(state)
        entitlement = self.store.read_entitlement()
        if check_in and entitlement:
            self._check_in(state, token, entitlement)
        self._save(state)
        return self.view()

    def _check_in(self, state: dict, token: str, entitlement: str) -> None:
        installation_id = self.installation_id()
        try:
            result = self.api.check_in(token, entitlement, installation_id)
        except ApiError as exc:
            raise self._handle(state, exc) from None
        now_iso = utc_now_iso(self.clock())
        if result["status"] == "valid":
            fresh = result["entitlement"]
            try:
                verify(fresh, trust=self.trust, installation_id=installation_id, now=self.clock())
            except EntitlementError as exc:
                # Keep the previous token; never store something we cannot verify.
                self._save(state)
                raise ServiceError("untrusted_entitlement") from exc
            self.store.write_entitlement(fresh)
            state["check_in"].update(status="valid", reason=None, at=now_iso)
            state["notice"] = None
            if result["license"]:
                state["licenses"] = [result["license"] if item["id"] == result["license"]["id"]
                                     else item for item in (state["licenses"] or [])] or [result["license"]]
        else:
            # revoked / invalid: delete the stored entitlement (contract 4.5).
            self.store.delete_entitlement()
            state["check_in"].update(status=result["status"], reason=result["reason"], at=now_iso)
            state["activation"] = None

    def sync_if_due(self, *, force: bool = False) -> StatusView | None:
        """Headless Companion sync launched by Blender; skips when not yet due."""
        state = self.state()
        due = parse_iso(state["connection"]["next_check_at"])
        if not force and due is not None and due > self.clock():
            return None
        return self.refresh()

    # Licences and devices -----------------------------------------------------------
    def activate(self, license_id: str, label: str) -> StatusView:
        token = self._require_token()
        state = self.state()
        installation_id = self.installation_id()
        label = " ".join(label.split())[:64] or default_label()
        try:
            activation, entitlement = self.api.activate(token, license_id, installation_id, label)
        except ApiError as exc:
            if exc.code == "device_limit":
                state["notice"] = "device_limit"
                self._save(state)
            raise self._handle(state, exc) from None
        try:
            verify(entitlement, trust=self.trust, installation_id=installation_id, now=self.clock())
        except EntitlementError as exc:
            self._save(state)
            raise ServiceError("untrusted_entitlement") from exc
        self.store.write_entitlement(entitlement)
        state["activation"] = {"license_id": license_id, "activation_id": activation["id"],
                               "label": activation["label"] or label,
                               "activated_at": activation["activated_at"]}
        state["check_in"].update(status="valid", reason=None, at=utc_now_iso(self.clock()))
        state["notice"] = None
        self._online(state)
        try:
            state["licenses"] = self.api.licenses(token)
        except ApiError:
            pass  # the activation succeeded; counts refresh on the next sync
        self._save(state)
        return self.view()

    def devices(self, license_id: str) -> list:
        token = self._require_token()
        state = self.state()
        try:
            return self.api.activations(token, license_id)
        except ApiError as exc:
            raise self._handle(state, exc) from None

    def deactivate(self, license_id: str, activation_id: str) -> StatusView:
        token = self._require_token()
        state = self.state()
        try:
            self.api.deactivate(token, license_id, activation_id)
        except ApiError as exc:
            raise self._handle(state, exc) from None
        if activation_id == self._current_activation(state)[1]:
            self.store.delete_entitlement()
            state["activation"] = None
            state["check_in"].update(status=None, reason=None, at=None)
        state["notice"] = None
        self._online(state)
        try:
            state["licenses"] = self.api.licenses(token)
        except ApiError:
            pass
        self._save(state)
        return self.view()

    def _current_activation(self, state: dict) -> tuple:
        """(license_id, activation_id) of this computer, from state or the token."""
        recorded = state["activation"] or {}
        if recorded.get("license_id") and recorded.get("activation_id"):
            return recorded["license_id"], recorded["activation_id"]
        token = self.store.read_entitlement()
        installation_id = self.store.read_installation_id()
        if token and installation_id:
            try:
                found = verify(token, trust=self.trust, installation_id=installation_id,
                               check_time=False)
                return found.license_id, found.activation_id
            except EntitlementError:
                pass
        return None, None

    def redeem(self, code: str) -> StatusView:
        token = self._require_token()
        state = self.state()
        code = code.strip()
        if not code or len(code) > 256:
            raise ServiceError("validation_error")
        try:
            self.api.redeem(token, code)
            state["licenses"] = self.api.licenses(token)
        except ApiError as exc:
            raise self._handle(state, exc) from None
        self._online(state)
        self._save(state)
        return self.view()

    # Sign-out (workflow 9) --------------------------------------------------------
    def sign_out(self, *, release_device: bool = False) -> str:
        token = self._token()
        state = self.state()
        released = False
        if release_device and token:
            license_id, activation_id = self._current_activation(state)
            if activation_id and license_id:
                try:
                    self.api.deactivate(token, license_id, activation_id)
                    released = True
                except ApiError as exc:
                    if exc.code != "not_found":
                        # Keep everything; the person can retry or sign out without releasing.
                        raise self._handle(state, exc) from None
                    released = True
        server_confirmed = True
        if token:
            try:
                self.api.logout(token)
            except ApiError as exc:
                server_confirmed = exc.session_dead  # an already dead session is fine
        try:
            self.vault.clear()
        except StorageError as exc:
            raise ServiceError("storage_unavailable") from exc
        state["account"].update(signed_in=False, email=None, email_verified=None,
                                session="none", session_expires_at=None)
        state["licenses"] = None
        state["notice"] = None
        if release_device:
            self.store.delete_entitlement()
            state["activation"] = None
            state["check_in"].update(status=None, reason=None, at=None)
        self._save(state)
        if not server_confirmed:
            return ("Signed out on this computer. The PolyWorks service could not be reached; "
                    "you can end the session in your PolyWorks account.")
        if released:
            return "Signed out and this computer was deactivated."
        if release_device:
            return ("Signed out and the license was removed from this computer. Manage the "
                    "remaining devices in your PolyWorks account.")
        return ("Signed out. This computer stays activated until its offline license needs "
                "renewal.") if self.store.read_entitlement() else "Signed out."
