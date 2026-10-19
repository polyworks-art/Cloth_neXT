# SPDX-License-Identifier: GPL-3.0-or-later
"""One state machine turning local files into the displayed account and services status.

The licence shown here covers optional PolyWorks services (GAIA, cloud bakes).
Cloth NeXt Classic is licensed through Superhive and never consults it.

Pure and side-effect free: no network, no vault, no ``bpy``. Blender and the
Companion both use it so they can never disagree. ``authorized`` is True only
for an entitlement this process verified itself (signature, installation and
time window); Companion-published state can describe or downgrade, never grant.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time

from . import messages
from .entitlements import Entitlement, EntitlementError, time_problem, verify
from .state import empty_state, parse_iso, sanitize
from .storage import ENTITLEMENT_FILE, PRESENCE_FILE, STATE_FILE, LocalStore
from .trust import TrustStore, load_trust

RENEWAL_WARNING_SECONDS = 5 * 24 * 3600  # add-on decision (contract 4.4: e.g. from day 25)
PRESENCE_FRESH_SECONDS = 20.0


class LicenseStatus:
    ACTIVE = "ACTIVE"
    OFFLINE_VERIFIED = "OFFLINE_VERIFIED"
    NOT_SIGNED_IN = "NOT_SIGNED_IN"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    NO_LICENSE = "NO_LICENSE"
    ACTIVATION_REQUIRED = "ACTIVATION_REQUIRED"
    DEVICE_DEACTIVATED = "DEVICE_DEACTIVATED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


LABELS = {
    LicenseStatus.ACTIVE: "Active",
    LicenseStatus.OFFLINE_VERIFIED: "Offline – verified",
    LicenseStatus.NOT_SIGNED_IN: "Not signed in",
    LicenseStatus.SESSION_EXPIRED: "Session expired",
    LicenseStatus.NO_LICENSE: "No service license",
    LicenseStatus.ACTIVATION_REQUIRED: "Computer not activated",
    LicenseStatus.DEVICE_DEACTIVATED: "Device deactivated",
    LicenseStatus.SUSPENDED: "License suspended",
    LicenseStatus.REVOKED: "License revoked",
    LicenseStatus.EXPIRED: "License expired",
    LicenseStatus.INVALID: "License could not be verified",
    LicenseStatus.UNKNOWN: "Status unknown",
}
AUTHORIZED_STATES = frozenset({LicenseStatus.ACTIVE, LicenseStatus.OFFLINE_VERIFIED})


@dataclass(frozen=True)
class StatusView:
    status: str
    authorized: bool
    message: str = ""
    warnings: tuple = ()
    # Account
    account_label: str = "Not signed in"
    account_identity: str = ""
    session_label: str = "No session"
    # Licence
    tier: str = ""
    seats: int | None = None
    devices_active: int | None = None
    device_limit: int | None = None
    expires_at: float | None = None
    license_source: str = ""  # "verified entitlement" | "reported by Companion" | ""
    device_label: str = ""
    # Connection
    companion_running: bool = False
    online: bool | None = None
    last_sync_at: float | None = None
    next_check_at: float | None = None
    connection_problem: str = ""
    offline_authorization: str = ""
    development_trust: bool = False
    details: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return LABELS[self.status]


def format_time(timestamp: float | None) -> str:
    if timestamp is None:
        return "never"
    moment = datetime.fromtimestamp(timestamp, timezone.utc).astimezone()
    return moment.strftime("%Y-%m-%d %H:%M")


def tier_label(tier: str | None) -> str:
    return tier.replace("_", " ").title() if tier else ""


def presence_running(presence, now: float) -> bool:
    if not isinstance(presence, dict):
        return False
    beat = presence.get("heartbeat_at")
    if type(beat) not in (int, float):
        return False
    return now - PRESENCE_FRESH_SECONDS <= beat <= now + 5


def _license_entry(state: dict, license_id: str | None):
    licenses = state.get("licenses") or []
    if license_id:
        for item in licenses:
            if item.get("id") == license_id:
                return item
    usable = [item for item in licenses if item.get("usable")]
    return usable[0] if len(usable) == 1 else None


def compute_view(*, state, token: str | None, installation_id: str | None,
                 trust: TrustStore, presence=None, now: float | None = None) -> StatusView:
    now = time.time() if now is None else now
    published = state is not None
    state = sanitize(state) if published else empty_state()
    account, connection, check_in = state["account"], state["connection"], state["check_in"]
    warnings: list[str] = []

    # Account --------------------------------------------------------------------
    session = account["session"]
    if session == "expired":
        account_label, session_label = "Session expired", "Sign in again in Cloth NeXt Companion"
    elif account["signed_in"] and session == "active":
        account_label, session_label = "Signed in", "Active"
    elif account["signed_in"]:
        account_label, session_label = "Signed in", "Not confirmed (service unreachable)"
    else:
        account_label, session_label = "Not signed in", "No session"
    if account["signed_in"] and account["session_expires_at"]:
        expiry = parse_iso(account["session_expires_at"])
        if expiry:
            session_label += f" · ends by {format_time(expiry)}"

    # Connection ------------------------------------------------------------------
    last_success = parse_iso(connection["last_success_at"])
    problem = ""
    if connection["online"] is False:
        problem = messages.OFFLINE
    elif connection["last_error"] == "rate_limited":
        problem = messages.COMPANION["rate_limited"]

    # Entitlement -----------------------------------------------------------------
    entitlement: Entitlement | None = None
    failure = ""
    if token:
        if not installation_id:
            failure = "wrong_installation"
        else:
            try:
                entitlement = verify(token, trust=trust, installation_id=installation_id,
                                     check_time=False)
            except EntitlementError as exc:
                failure = exc.code
    timing = time_problem(entitlement, now) if entitlement else None
    revoked_after = (check_in["status"] in ("revoked", "invalid") and entitlement is not None
                     and (parse_iso(check_in["at"]) or 0) >= entitlement.issued_at)

    entry = _license_entry(state, entitlement.license_id if entitlement else (
        (state.get("activation") or {}).get("license_id")))
    message = ""
    if entitlement and not timing and not revoked_after:
        online_now = connection["online"] is True and check_in["status"] == "valid"
        status = LicenseStatus.ACTIVE if online_now else LicenseStatus.OFFLINE_VERIFIED
        remaining = entitlement.expires_at - now
        if remaining < RENEWAL_WARNING_SECONDS:
            warnings.append("Renewal due for PolyWorks services: connect to the internet and open Cloth NeXt "
                            f"Companion before {format_time(entitlement.expires_at)}.")
        if status == LicenseStatus.OFFLINE_VERIFIED and connection["online"] is False:
            message = messages.OFFLINE
    elif entitlement and revoked_after:
        status = _revocation_status(check_in)
        message = (messages.revocation_message(check_in["reason"])
                   if check_in["status"] == "revoked" else messages.REVOKED)
    elif entitlement and timing == "expired":
        status = LicenseStatus.EXPIRED
        message = ("The offline authorization for PolyWorks services has expired. Connect to "
                   "the internet and open Cloth NeXt Companion to renew it.")
    elif entitlement and timing == "not_yet_valid":
        status = LicenseStatus.INVALID
        message = ("The PolyWorks service license is not valid yet. Check the date and time "
                   "of this computer.")
    elif token:
        status = LicenseStatus.INVALID
        message = ("The saved PolyWorks service license could not be verified on this computer "
                   f"({failure.replace('_', ' ')}). Open Cloth NeXt Companion to activate again.")
    elif check_in["status"] == "revoked":
        status = _revocation_status(check_in)
        message = messages.revocation_message(check_in["reason"])
    elif check_in["status"] == "invalid":
        status = LicenseStatus.ACTIVATION_REQUIRED
        message = ("The previous PolyWorks service license on this computer was not accepted. "
                   "Activate it again in Cloth NeXt Companion.")
    elif session == "expired":
        status, message = LicenseStatus.SESSION_EXPIRED, messages.SESSION_EXPIRED
    elif not published or not account["signed_in"]:
        status = LicenseStatus.NOT_SIGNED_IN
        message = ("Optional: sign in through Cloth NeXt Companion to use GAIA and online "
                   "services.")
    elif state["licenses"] is None:
        status = LicenseStatus.UNKNOWN
        message = "Cloth NeXt Companion has not loaded your service licenses yet."
    elif not state["licenses"]:
        status = LicenseStatus.NO_LICENSE
        message = ("Your PolyWorks account has no service license. Classic simulation "
                   "does not need one.")
    elif any(item["usable"] for item in state["licenses"]):
        status = LicenseStatus.ACTIVATION_REQUIRED
        message = ("Activate this computer in Cloth NeXt Companion to use PolyWorks services."
                   if state["notice"] != "device_limit"
                   else messages.DEVICE_LIMIT_BLENDER)
    elif any(item["state"] == "suspended" for item in state["licenses"]):
        status, message = LicenseStatus.SUSPENDED, messages.REVOKED
    else:
        status, message = LicenseStatus.REVOKED, messages.REVOKED

    authorized = status in AUTHORIZED_STATES
    if session == "expired" and authorized:
        warnings.append("Your PolyWorks session has expired. PolyWorks services keep working "
                        "offline until the authorization expires; sign in again in Cloth NeXt "
                        "Companion to renew it.")
    if entitlement and entitlement.development_key:
        warnings.append("Verified with a development key: this is not a production license.")

    if authorized:
        offline_authorization = f"Verified on this computer until {format_time(entitlement.expires_at)}"
    elif entitlement and timing == "expired":
        offline_authorization = f"Expired on {format_time(entitlement.expires_at)}"
    else:
        offline_authorization = "None"

    activation = state.get("activation") or {}
    return StatusView(
        status=status, authorized=authorized, message=message, warnings=tuple(warnings),
        account_label=account_label, account_identity=account["email"] or "",
        session_label=session_label,
        tier=tier_label(entitlement.tier if entitlement else (entry or {}).get("tier")),
        seats=entitlement.seats if entitlement and entitlement.seats is not None
        else (entry or {}).get("seats"),
        devices_active=(entry or {}).get("devices_active"),
        device_limit=(entry or {}).get("device_limit"),
        expires_at=entitlement.expires_at if entitlement else None,
        license_source="verified entitlement" if entitlement else (
            "reported by Cloth NeXt Companion" if entry else ""),
        device_label=activation.get("label") or "",
        companion_running=presence_running(presence, now),
        online=connection["online"], last_sync_at=last_success,
        next_check_at=parse_iso(connection["next_check_at"]),
        connection_problem=problem, offline_authorization=offline_authorization,
        development_trust=bool(entitlement and entitlement.development_key),
        details={"verification": failure or (timing or ("ok" if entitlement else "none"))})


def _revocation_status(check_in) -> str:
    if check_in["status"] == "invalid":
        return LicenseStatus.ACTIVATION_REQUIRED
    reason = check_in["reason"]
    if reason == "device_deactivated":
        return LicenseStatus.DEVICE_DEACTIVATED
    if reason == "license_suspended":
        return LicenseStatus.SUSPENDED
    return LicenseStatus.REVOKED


def read_view(store: LocalStore | None = None, *, trust: TrustStore | None = None,
              now: float | None = None) -> StatusView:
    """Read the local files and compute the view. Local file IO only."""
    store = store or LocalStore()
    return compute_view(
        state=store.read_json(STATE_FILE), token=store.read_entitlement(),
        installation_id=store.read_installation_id(), trust=trust or load_trust(),
        presence=store.read_json(PRESENCE_FILE, 4096), now=now)


def file_signature(store: LocalStore) -> tuple:
    """Cheap change detector (stat only) for Blender's polling timer."""
    result = []
    for name in (STATE_FILE, ENTITLEMENT_FILE, PRESENCE_FILE, "installation-id"):
        try:
            info = store.path(name).stat()
            result.append((name, info.st_mtime_ns, info.st_size))
        except OSError:
            result.append((name, None, None))
    return tuple(result)
