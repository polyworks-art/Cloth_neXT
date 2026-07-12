# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compose process readiness and the verified PPF status query."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord
from ..core.state import ApplicationState
from .compatibility import CompatibilityResult, validate_versions
from .models import ConnectionOwnership
from .process import SolverProcessManager
from .status import ParsedStatus, application_state_hint, parse_status
from .transport import TransportConfig, query_status


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    reachable: bool
    compatible: bool
    ownership: ConnectionOwnership
    process_running: bool | None
    host: str
    port: int
    package_version: str | None
    protocol_version: str | None
    schema_version: str | None
    wire_status: str | None
    application_state: ApplicationState | None
    process_id: int | None
    exit_code: int | None
    last_error: ErrorRecord | None
    checked_at: datetime


def port_reachable(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def query_health(
    *, host: str, port: int, project_name: str,
    ownership: ConnectionOwnership, transport: TransportConfig,
    local_versions: tuple[str, str, str] | None = None,
    process_running: bool | None = None, process_id: int | None = None,
    exit_code: int | None = None,
) -> HealthSnapshot:
    checked = datetime.now(timezone.utc)
    try:
        response = query_status(host, port, project_name, transport)
        parsed = parse_status(response)
        package, executable_protocol, schema = local_versions or (None, None, None)
        compatibility = validate_versions(parsed.protocol_version, schema, package)
        if executable_protocol is not None and executable_protocol != parsed.protocol_version:
            compatibility = validate_versions(executable_protocol, schema, package)
        if response.get("error"):
            raise ValueError(f"PPF error response: {response['error']}")
        error = compatibility.error
        if compatibility.schema_compatible is None:
            error = ErrorRecord.create(
                category=ErrorCategory.PROTOCOL_COMPATIBILITY,
                user_message="The server protocol matches, but its scene schema cannot be verified remotely.",
                technical_message="PPF 0.11 status responses do not expose schema_version or package_version",
                recommended_action="For full verification, configure the matching local executable from pinned commit 7193f158.",
                recoverable=True,
            )
        return HealthSnapshot(True, compatibility.fully_compatible, ownership, process_running,
            host, port, package, parsed.protocol_version, schema, parsed.wire_status.value,
            application_state_hint(parsed.wire_status), process_id, exit_code, error, checked)
    except (ClothNextError, ValueError) as exc:
        error = exc.record if isinstance(exc, ClothNextError) else ErrorRecord.create(
            category=ErrorCategory.SOLVER_CONNECTION,
            user_message="The service on the configured port is not a valid PPF 0.11 server.",
            technical_message=str(exc),
            recommended_action="Stop the conflicting service or choose another port.",
            recoverable=True,
            exception=exc,
        )
        return HealthSnapshot(False, False, ownership, process_running, host, port, None,
            None, None, None, None, process_id, exit_code, error, checked)


def start_owned_and_wait(manager: SolverProcessManager, project_name: str = "cloth-next-health", poll_interval: float = 0.05) -> HealthSnapshot:
    cfg = manager.config
    if port_reachable(cfg.host, cfg.port, cfg.connect_timeout):
        existing = query_health(host=cfg.host, port=cfg.port, project_name=project_name,
            ownership=ConnectionOwnership.EXTERNAL_SERVER,
            transport=TransportConfig(cfg.connect_timeout, cfg.read_timeout))
        if existing.protocol_version is not None:
            return existing
        raise ClothNextError(existing.last_error)  # type: ignore[arg-type]
    versions = manager.executable_version()
    manager.start()
    deadline = time.monotonic() + cfg.startup_timeout
    try:
        while time.monotonic() < deadline:
            poll = manager.poll()
            if not poll.running:
                raise manager.early_exit_error(poll)
            if poll.progress.ready:
                health = query_health(host=cfg.host, port=cfg.port, project_name=project_name,
                    ownership=ConnectionOwnership.OWNED_PROCESS,
                    transport=TransportConfig(cfg.connect_timeout, cfg.read_timeout),
                    local_versions=versions, process_running=True, process_id=poll.process_id)
                if health.reachable:
                    if not health.compatible:
                        # An incompatible solver is never reported as started.
                        raise ClothNextError(health.last_error or ErrorRecord.create(
                            category=ErrorCategory.PROTOCOL_COMPATIBILITY,
                            user_message="The solver started but is not compatible "
                                         "with this Cloth NeXt build.",
                            technical_message=(
                                f"protocol={health.protocol_version!r}, "
                                f"schema={health.schema_version!r}, "
                                f"package={health.package_version!r}"),
                            recommended_action="Install the compatible solver version "
                                               "listed in the compatibility manifest.",
                            recoverable=True,
                        ))
                    return health
            time.sleep(poll_interval)
        raise ClothNextError(ErrorRecord.create(
            category=ErrorCategory.SOLVER_CONNECTION,
            user_message="The PPF solver did not become ready in time.",
            technical_message=f"startup timeout after {cfg.startup_timeout}s; progress={manager.poll().progress.tail}",
            recommended_action="Inspect solver logs and verify that the configured port is available.",
            recoverable=True,
        ))
    except Exception:
        manager.stop()
        raise
