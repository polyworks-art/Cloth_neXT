# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compose process readiness and the verified PPF status query."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord
from ..core.logging import get_logger, log_with_context
from ..core.state import ApplicationState
from .compatibility import (CompatibilityResult, DEFAULT_PROTOCOL_PROFILE,
                            ProtocolProfile, protocol_profile,
                            validate_versions)
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
    expected_profile: ProtocolProfile | None = None,
    process_running: bool | None = None, process_id: int | None = None,
    exit_code: int | None = None,
) -> HealthSnapshot:
    checked = datetime.now(timezone.utc)
    try:
        response = query_status(host, port, project_name, transport)
        parsed = parse_status(response)
        package, executable_protocol, schema = local_versions or (None, None, None)
        profile = expected_profile or (
            protocol_profile(executable_protocol, schema)
            if executable_protocol is not None and schema is not None
            else None) or DEFAULT_PROTOCOL_PROFILE
        compatibility = validate_versions(
            parsed.protocol_version, schema, package, profile=profile)
        if executable_protocol is not None and executable_protocol != parsed.protocol_version:
            compatibility = validate_versions(
                executable_protocol, schema, package, profile=profile)
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
            user_message="The service on the configured port is not a valid supported PPF server.",
            technical_message=str(exc),
            recommended_action="Stop the conflicting service or choose another port.",
            recoverable=True,
            exception=exc,
        )
        return HealthSnapshot(False, False, ownership, process_running, host, port, None,
            None, None, None, None, process_id, exit_code, error, checked)


def start_owned_and_wait(manager: SolverProcessManager,
                         project_name: str = "cloth-next-health",
                         poll_interval: float = 0.05,
                         expected_profile: ProtocolProfile | None = None,
                         ) -> HealthSnapshot:
    cfg = manager.config
    logger = get_logger("ppf.health")
    startup_started = time.monotonic()
    if port_reachable(cfg.host, cfg.port, cfg.connect_timeout):
        existing = query_health(host=cfg.host, port=cfg.port, project_name=project_name,
            ownership=ConnectionOwnership.EXTERNAL_SERVER,
            transport=TransportConfig(cfg.connect_timeout, cfg.read_timeout))
        raise ClothNextError(ErrorRecord.create(
            category=ErrorCategory.SOLVER_CONNECTION,
            user_message=f"Port {cfg.port} is already in use.",
            technical_message=(
                f"owned launch refused before Popen because "
                f"{cfg.host}:{cfg.port} accepted a connection; "
                f"service_protocol={existing.protocol_version!r}; "
                f"service_status={existing.wire_status!r}; "
                f"service_error={existing.last_error}"),
            recommended_action=(
                "Close the process using the port or retry with a new port."),
            recoverable=True,
            context={"failure_kind": "PORT_ALREADY_OCCUPIED",
                     "host": cfg.host, "port": cfg.port}))
    versions = manager.executable_version()
    profile = expected_profile or protocol_profile(versions[1], versions[2])
    manager.start()
    deadline = startup_started + cfg.startup_timeout
    attempts = 0
    last_health: HealthSnapshot | None = None
    try:
        if profile is None:
            raise ClothNextError(ErrorRecord.create(
                category=ErrorCategory.PROTOCOL_COMPATIBILITY,
                user_message="The selected solver is not compatible with "
                             "this Cloth NeXt build.",
                technical_message=(
                    f"protocol={versions[1]!r}, schema={versions[2]!r}"),
                recommended_action="Select a supported solver installation.",
                recoverable=True))
        while time.monotonic() < deadline:
            poll = manager.poll()
            if not poll.running:
                raise manager.early_exit_error(poll)
            if poll.progress.ready:
                attempts += 1
                elapsed = time.monotonic() - startup_started
                health = query_health(host=cfg.host, port=cfg.port, project_name=project_name,
                    ownership=ConnectionOwnership.OWNED_PROCESS,
                    transport=TransportConfig(cfg.connect_timeout, cfg.read_timeout),
                    local_versions=versions, expected_profile=profile,
                    process_running=True, process_id=poll.process_id)
                last_health = health
                log_with_context(logger, 20, "startup health attempt", {
                    "launch_id": getattr(poll, "launch_id", ""),
                    "process_id": poll.process_id,
                    "attempt": attempts,
                    "elapsed_seconds": round(elapsed, 6),
                    "host": cfg.host,
                    "port": cfg.port,
                    "reachable": health.reachable,
                    "compatible": health.compatible,
                    "protocol": health.protocol_version,
                    "schema": health.schema_version,
                    "wire_status": health.wire_status,
                    "last_error": (
                        health.last_error.technical_message
                        if health.last_error is not None else ""),
                })
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
                    log_with_context(logger, 20, "solver ready", {
                        "launch_id": getattr(poll, "launch_id", ""),
                        "process_id": poll.process_id,
                        "attempts": attempts,
                        "elapsed_seconds": round(elapsed, 6),
                        "protocol": health.protocol_version,
                        "schema": health.schema_version,
                    })
                    return health
            time.sleep(poll_interval)
        raise ClothNextError(ErrorRecord.create(
            category=ErrorCategory.SOLVER_CONNECTION,
            user_message=(
                f"The solver started but did not become ready within "
                f"{cfg.startup_timeout:g} seconds."),
            technical_message=(
                f"failure_kind=STARTUP_TIMEOUT_ALIVE; "
                f"startup timeout after {cfg.startup_timeout}s; "
                f"connection_attempts={attempts}; "
                f"last_health={last_health}; "
                f"poll={manager.poll()}"),
            recommended_action="Inspect solver logs and verify that the configured port is available.",
            recoverable=True,
            context={"failure_kind": "STARTUP_TIMEOUT_ALIVE",
                     "connection_attempts": attempts,
                     "host": cfg.host, "port": cfg.port,
                     "startup_timeout": cfg.startup_timeout},
        ))
    except Exception as primary:
        try:
            manager.stop()
        except BaseException as cleanup:
            primary.add_note(
                "Additional owned-process cleanup failure: "
                f"{type(cleanup).__name__}: {cleanup}")
        raise
