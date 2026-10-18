# SPDX-License-Identifier: GPL-3.0-or-later
"""User-facing wording for every contract error code and local condition.

Server ``message`` texts are never shown; the client branches on codes only.
"""
from __future__ import annotations

OFFLINE = ("Unable to reach the PolyWorks service. Your existing offline authorization "
           "keeps working if still valid. Classic simulation is not affected.")
DEVICE_LIMIT_BLENDER = ("All available device activations are currently in use. "
                        "Manage your devices in Cloth NeXt Companion.")
DEVICE_LIMIT = ("All available device activations are currently in use. Deactivate a "
                "device below or in your PolyWorks account, then activate again.")
SESSION_EXPIRED = ("Your PolyWorks session has expired. Sign in again through "
                   "Cloth NeXt Companion.")
REVOKED = ("This PolyWorks service license is no longer valid. Classic simulation is not "
           "affected. Open Cloth NeXt Companion for details.")

REVOCATION_REASONS = {
    "refunded": "The purchase for this license was refunded.",
    "license_revoked": "This license was revoked.",
    "license_suspended": "This license is suspended.",
    "seat_released": "Your seat on this license was released.",
    "device_deactivated": "This computer was deactivated. Activate it again in Cloth NeXt Companion.",
}

COMPANION = {
    "network_unavailable": OFFLINE,
    "service_unavailable": OFFLINE,
    "server_error": "The PolyWorks service had a temporary problem. Please try again later. "
                    "Your existing offline authorization keeps working if still valid.",
    "feature_disabled": ("License signing is temporarily unavailable on the PolyWorks service. "
                         "Your existing offline authorization keeps working if still valid."),
    "internal_error": "The PolyWorks service had an internal error. Please try again later.",
    "invalid_response": "The PolyWorks service sent a response this version cannot read. "
                        "Nothing was changed locally.",
    "rate_limited": "Too many requests to the PolyWorks service. Please wait a moment and try again.",
    "authorization_pending": "Waiting for you to approve the sign-in in your browser.",
    "slow_down": "Waiting for you to approve the sign-in in your browser.",
    "access_denied": "The sign-in request was declined in the browser.",
    "expired_token": "The sign-in request expired. Start the sign-in again.",
    "invalid_device_code": "This sign-in request is no longer valid. Start the sign-in again.",
    "invalid_credentials": "The email address or password is not correct.",
    "not_authenticated": SESSION_EXPIRED,
    "forbidden": "Your PolyWorks account is not allowed to do this. Check that your email "
                 "address is verified and that you hold a seat on this license.",
    "not_found": "This license or device no longer exists. Refresh to load the current state.",
    "device_limit": DEVICE_LIMIT,
    "license_inactive": "This license is suspended, revoked or refunded, so it cannot be "
                        "activated. See your PolyWorks account for details.",
    "conflict": "The PolyWorks service reported a conflict. Refresh and try again.",
    "payload_too_large": "The request was too large.",
    "validation_error": "The PolyWorks service did not accept the entered values.",
    "untrusted_entitlement": "The license sent by the service could not be verified on this "
                             "computer, so it was not saved. Please contact support.",
    "storage_unavailable": "The local account storage could not be used. Nothing was sent.",
    "busy": "Another Cloth NeXt Companion window is updating the account. Try again in a moment.",
    "sign_in_required": "Sign in to your PolyWorks account first.",
    "mfa_invalid": "The code was not accepted. Check your authenticator app or recovery code.",
}


def companion_message(code: str, request_id: str = "") -> str:
    text = COMPANION.get(code, "The PolyWorks service could not complete this request.")
    if request_id and code in ("internal_error", "server_error"):
        text += f" Reference: {request_id}"
    return text


def revocation_message(reason: str | None) -> str:
    # Contract 8: an unknown reason is treated as revoked.
    return REVOCATION_REASONS.get(reason or "", REVOCATION_REASONS["license_revoked"])
