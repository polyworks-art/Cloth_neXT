# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Parse supported PPF status responses and map only unambiguous states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.state import ApplicationState


class WireStatus(Enum):
    NO_DATA = "NO_DATA"
    NO_BUILD = "NO_BUILD"
    BUILDING = "BUILDING"
    READY = "READY"
    RESUMABLE = "RESUMABLE"
    FAILED = "FAILED"
    BUSY = "BUSY"
    SAVE_AND_QUIT = "SAVE_AND_QUIT"


@dataclass(frozen=True, slots=True)
class ParsedStatus:
    wire_status: WireStatus
    protocol_version: str
    error: str = ""
    crash_kind: str = ""
    frame: int = 0


def parse_status(response: dict[str, object]) -> ParsedStatus:
    protocol = response.get("protocol_version")
    status = response.get("status")
    if not isinstance(protocol, str) or not protocol:
        raise ValueError("response missing protocol_version")
    if not isinstance(status, str):
        raise ValueError("response missing status")
    error = response.get("error", "")
    crash_kind = response.get("crash_kind", "")
    if error is not None and not isinstance(error, str):
        raise ValueError("response error must be text")
    if crash_kind is not None and not isinstance(crash_kind, str):
        raise ValueError("response crash_kind must be text")
    return ParsedStatus(
        wire_status=WireStatus(status),
        protocol_version=protocol,
        error=error or "",
        crash_kind=crash_kind or "",
        frame=int(response.get("frame", 0)),
    )


def application_state_hint(status: WireStatus) -> ApplicationState | None:
    """Return a UI hint only where the mapping is operationally unambiguous."""
    return {
        WireStatus.BUILDING: ApplicationState.BUILDING,
        WireStatus.READY: ApplicationState.READY,
        WireStatus.RESUMABLE: ApplicationState.PAUSED,
        WireStatus.FAILED: ApplicationState.ERROR,
        WireStatus.BUSY: ApplicationState.SIMULATING,
        WireStatus.SAVE_AND_QUIT: ApplicationState.CANCELLING,
    }.get(status)

