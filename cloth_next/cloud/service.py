# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which Cloud backend this add-on talks to, and whether Cloud is usable.

Cloud Bakes ship enabled but locked: ``availability`` only reports Cloud as
usable once the PolyWorks Cloud server itself answered its capability check
with Cloud enabled and execution enabled. Until then (no answer, an error,
or the server's switch still off) the Cloud location stays locked with a
specific reason, and nothing is packaged or sent. Tests and the Phase 0 tool
install the deterministic test backend explicitly. Nothing here touches the
network or the filesystem; draw code may call it.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Callable

from . import transport

PENDING_REASON = ("Cloud Bakes are not available yet: the PolyWorks Cloud "
                  "service is not connected to this version.")
LOCKED_REASON = ("Cloud Bakes are locked until the PolyWorks Cloud service "
                 "reports that it is ready.")
CHECKING_REASON = "Checking Cloud availability…"
# Answers the artist can act on (sign in, access); any other failure keeps
# Cloud locked with LOCKED_REASON.
_ACCOUNT_CODES = frozenset({"not_authenticated", "session_expired", "forbidden",
                            "insufficient_scope", "cloud_access_required"})


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    reason: str = ""
    capabilities: transport.Capabilities | None = None


_lock = Lock()
_factory: Callable[[], transport.CloudBackend] | None = None
_capabilities: transport.Capabilities | None = None
_last_error: str = ""


CLOUD_API_ENV = "CLOTH_NEXT_CLOUD_API"
DEFAULT_ORIGIN = "https://api.cloth-next.de"
_OFF_VALUES = frozenset({"0", "off", "false", "disabled", "none"})


def default_factory(environ=None):
    """The HTTP backend for the PolyWorks Cloud, else ``None``.

    The origin is ``https://api.cloth-next.de``. ``CLOTH_NEXT_CLOUD_API``
    may name another origin (a loopback test server) or switch Cloud off
    (``off``). Whether Cloud may be used is decided by the server's
    capability answer, see ``availability``. The bearer token is read from
    the existing account vault on the worker thread for every request; it
    never passes through Blender's UI.
    """
    import os
    environ = os.environ if environ is None else environ
    origin = str(environ.get(CLOUD_API_ENV, "") or "").strip()
    if origin.lower() in _OFF_VALUES:
        return None
    origin = origin or DEFAULT_ORIGIN
    from .http_v1 import HttpCloudBackend, validate_origin
    origin = validate_origin(origin)

    def token() -> str | None:
        from ..polyworks.service import AccountService
        return AccountService()._token()
    backend = HttpCloudBackend(token, origin=origin)
    return lambda: backend


def install_backend(factory: Callable[[], transport.CloudBackend] | None) -> None:
    """Install (or remove with ``None``) the backend factory."""
    global _factory, _capabilities, _last_error
    with _lock:
        _factory = factory
        _capabilities = None
        _last_error = ""


def backend() -> transport.CloudBackend | None:
    with _lock:
        factory = _factory
    return factory() if factory is not None else None


def record_capabilities(capabilities: transport.Capabilities | None,
                        error_code: str = "") -> None:
    """Store the last capability check (done on a worker thread)."""
    global _capabilities, _last_error
    with _lock:
        _capabilities = capabilities
        _last_error = error_code


def availability(engine_build_id: str | None = None) -> Availability:
    """Cheap, cached view for panels and the Bake button."""
    with _lock:
        factory, capabilities, error = _factory, _capabilities, _last_error
    if factory is None:
        return Availability(False, PENDING_REASON)
    if error in _ACCOUNT_CODES:
        from .errors import describe
        return Availability(False, describe(error)[2])
    if error:
        # No usable answer from the server keeps Cloud locked (not an error
        # the artist has to act on); the check repeats in the background.
        return Availability(False, LOCKED_REASON)
    if capabilities is None:
        return Availability(False, CHECKING_REASON)
    if not capabilities.enabled or not capabilities.execution_enabled:
        return Availability(False, LOCKED_REASON, capabilities)
    if engine_build_id is not None and engine_build_id not in capabilities.builds:
        from .errors import describe
        return Availability(False, describe("build_unavailable")[2], capabilities)
    return Availability(True, "", capabilities)


def unlocked() -> bool:
    """True once the server confirmed Cloud and execution are enabled."""
    with _lock:
        capabilities, error = _capabilities, _last_error
    return bool(capabilities is not None and not error and capabilities.enabled
                and capabilities.execution_enabled)


def checked() -> bool:
    """A capability answer (or failure) is on record."""
    with _lock:
        return _capabilities is not None or bool(_last_error)


def describe_disabled() -> str:
    from .errors import describe
    return describe("feature_disabled")[2]
