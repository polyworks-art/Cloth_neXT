# SPDX-License-Identifier: GPL-3.0-or-later
"""Bug reports for ``POST /v1/bug-reports``: field rules and user wording. No ``bpy``.

The server accepts exactly these fields and rejects any other one. Lengths
count characters; control characters other than newline and tab are refused,
so they are removed here before sending (Windows line breaks become ``\\n``).
The add-on fills ``addon_version``, ``blender_version`` and
``operating_system`` itself; those are shortened instead of refused.
"""
from __future__ import annotations

import math
import platform
import unicodedata

PATH = "/v1/bug-reports"
# field: (minimum, maximum) characters; a minimum above 0 means required.
LIMITS = {
    "title": (3, 160),
    "description": (10, 10000),
    "steps": (0, 5000),
    "addon_version": (0, 64),
    "blender_version": (0, 64),
    "operating_system": (0, 128),
}
FIELDS = tuple(LIMITS)
USER_FIELDS = ("title", "description", "steps")
AUTOMATIC_FIELDS = ("addon_version", "blender_version", "operating_system")
LABELS = {"title": "Title", "description": "Description", "steps": "Steps to reproduce",
          "addon_version": "Add-on version", "blender_version": "Blender version",
          "operating_system": "Operating system"}
HOURLY_LIMIT = 10


class BugReportInvalid(ValueError):
    """A field breaks a server rule; nothing was sent."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


class BugReportError(Exception):
    """Sending failed; ``message`` is ready for the user."""

    def __init__(self, code: str, *, retry_after=None, details=None, request_id: str = ""):
        self.code = code
        self.retry_after = retry_after
        self.message = failure_message(code, retry_after=retry_after, details=details,
                                       request_id=request_id)
        super().__init__(self.message)


def clean_text(value) -> str:
    """Normalise line breaks, drop forbidden control characters, trim the ends."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text
                   if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    return text.strip()


def operating_system() -> str:
    system = platform.system() or "Unknown OS"
    release = platform.release()
    version = platform.version()
    machine = platform.machine()
    text = " ".join(part for part in (system, release) if part)
    extra = ", ".join(part for part in (version, machine) if part)
    return clean_text(f"{text} ({extra})" if extra else text)[:LIMITS["operating_system"][1]]


def environment_fields(addon_version: str, blender_version: str) -> dict:
    return {"addon_version": clean_text(addon_version)[:LIMITS["addon_version"][1]],
            "blender_version": clean_text(blender_version)[:LIMITS["blender_version"][1]],
            "operating_system": operating_system()}


def field_problem(field: str, value: str) -> str:
    """Empty when ``value`` (already cleaned) fits; otherwise a short English sentence."""
    low, high = LIMITS[field]
    length = len(value)
    name = LABELS[field]
    if low and length == 0:
        return f"{name} is required."
    if length < low:
        return f"{name} needs at least {low} characters ({length} so far)."
    if length > high:
        return f"{name} is too long: {length} of at most {high} characters."
    return ""


def build_report(fields: dict) -> dict:
    """The exact request body, or ``BugReportInvalid`` for the first broken user field."""
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise BugReportInvalid(sorted(unknown)[0], "Unknown bug report field.")
    body = {}
    for field in FIELDS:
        value = clean_text(fields.get(field, ""))
        if field in AUTOMATIC_FIELDS:
            value = value[:LIMITS[field][1]]
        problem = field_problem(field, value)
        if problem:
            raise BugReportInvalid(field, problem)
        if value or LIMITS[field][0]:
            body[field] = value
    return body


def receipt_from(value) -> dict:
    """201 body ``{"id", "submitted_at"}`` reduced to two strings."""
    if not isinstance(value, dict):
        return {"id": "", "submitted_at": ""}
    report_id = value.get("id")
    submitted = value.get("submitted_at")
    return {"id": str(report_id) if isinstance(report_id, (str, int)) and
            not isinstance(report_id, bool) else "",
            "submitted_at": submitted if isinstance(submitted, str) else ""}


def _wait_text(seconds) -> str:
    if seconds is None:
        return "later"
    minutes = max(1, math.ceil(float(seconds) / 60.0))
    return "in about 1 minute" if minutes == 1 else f"in about {minutes} minutes"


def _rejected_fields(details) -> list[str]:
    """Field names out of a 422 ``details`` value, whatever shape the server uses."""
    names = []

    def add(name):
        if isinstance(name, str) and name in LABELS and LABELS[name] not in names:
            names.append(LABELS[name])

    items = details if isinstance(details, list) else [details]
    for item in items:
        if isinstance(item, dict):
            add(item.get("field"))
            location = item.get("loc")
            if isinstance(location, (list, tuple)) and location:
                add(location[-1])
            for key in item:
                add(key)
        else:
            add(item)
    return names


def failure_message(code: str, *, retry_after=None, details=None, request_id: str = "") -> str:
    """User wording for every outcome other than 201. The draft is always kept."""
    kept = " Your text is kept, so you can send it again."
    if code == "sign_in_required":
        return ("Connect your PolyWorks account first (Preferences > Cloth NeXt > Account). "
                "Bug reports are sent with your account." + kept)
    if code == "not_authenticated":
        return ("Your PolyWorks session has expired. Connect your PolyWorks account again, "
                "then send the report." + kept)
    if code == "rate_limited":
        return (f"You have sent {HOURLY_LIMIT} bug reports within the last hour. Please try "
                f"again {_wait_text(retry_after)}." + kept)
    if code in ("validation_error", "invalid_request", "rejected"):
        fields = _rejected_fields(details)
        which = f" Check: {', '.join(fields)}." if fields else ""
        return "The PolyWorks service did not accept the report." + which + kept
    if code in ("network_unavailable", "service_unavailable"):
        return "The PolyWorks service could not be reached. Check your connection." + kept
    if code == "bake_active":
        return "Bug reports cannot be sent while a Bake is running." + kept
    if code == "email_unverified":
        return "Verify the email address of your PolyWorks account first." + kept
    if code == "forbidden":
        return "Your PolyWorks account is not allowed to send bug reports." + kept
    if code == "storage_unavailable":
        return "The local PolyWorks account storage could not be read. Nothing was sent." + kept
    reference = f" Reference: {request_id}." if request_id else ""
    return ("The PolyWorks service could not take the report right now. Please try again "
            "later." + reference + kept)


def success_message(receipt: dict) -> str:
    report_id = receipt.get("id") or ""
    return "Thank you, your bug report was sent." + (f" Report ID: {report_id}." if report_id else "")
