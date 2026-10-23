# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which Cloud backend this add-on talks to, and whether Cloud is usable.

No production backend exists until the ``/v1/cloud/`` contract is final, so
``availability`` reports Cloud as unavailable with a specific reason. Tests
and the Phase 0 tool install the deterministic test backend explicitly.
Nothing here touches the network or the filesystem; draw code may call it.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Callable

from . import transport

PENDING_REASON = ("Cloud Bakes are not available yet: the PolyWorks Cloud "
                  "service is not connected to this version.")


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


def default_factory(environ=None):
    """The HTTP backend when Cloud routing is switched on, else ``None``.

    Cloud API v1 is a pilot: production routing and submission are not
    enabled yet, so the add-on only talks to it when ``CLOTH_NEXT_CLOUD_API``
    names the origin (``https://api.cloth-next.de`` or a loopback test
    server). The bearer token is read from the existing account vault on the
    worker thread for every request; it never passes through Blender's UI.
    """
    import os
    environ = os.environ if environ is None else environ
    origin = str(environ.get(CLOUD_API_ENV, "") or "").strip()
    if not origin:
        return None
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
    if error:
        from .errors import describe
        return Availability(False, describe(error)[2])
    if capabilities is None:
        return Availability(False, "Checking Cloud availability…")
    if not capabilities.enabled:
        return Availability(False, describe_disabled())
    if engine_build_id is not None and engine_build_id not in capabilities.builds:
        from .errors import describe
        return Availability(False, describe("build_unavailable")[2], capabilities)
    return Availability(True, "", capabilities)


def describe_disabled() -> str:
    from .errors import describe
    return describe("feature_disabled")[2]
