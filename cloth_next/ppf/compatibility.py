# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exact PPF baseline validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..core.errors import ErrorCategory, ErrorRecord

_VERSION_RE = re.compile(r"(?P<package>\d+\.\d+\.\d+) \(protocol v(?P<protocol>[^,]+), schema v(?P<schema>[^)]+)\)")


@dataclass(frozen=True, slots=True)
class ProtocolProfile:
    protocol_version: str
    schema_version: str
    package_version: str | None
    display_name: str
    adapter_id: str = ""


@lru_cache(maxsize=32)
def protocol_profile(protocol: str, schema: str) -> ProtocolProfile | None:
    from ..updater.solver_manifest import load_bundled_manifest
    from .adapters import ADAPTERS
    for entry in load_bundled_manifest().platforms:
        if entry.protocol_version == protocol and entry.schema_version == schema:
            adapter = ADAPTERS.get(entry.adapter_id)
            if adapter is None or adapter.supported_schema != schema:
                return None
            return ProtocolProfile(protocol, schema, entry.solver_package_version,
                                   f"Solver Protocol {protocol} / Schema {schema}",
                                   adapter.id)
    return None


@dataclass(frozen=True, slots=True)
class CompatibilityResult:
    protocol_version: str | None
    schema_version: str | None
    package_version: str | None
    protocol_compatible: bool
    schema_compatible: bool | None
    package_matches_baseline: bool | None
    error: ErrorRecord | None = None

    @property
    def fully_compatible(self) -> bool:
        return (self.protocol_compatible and self.schema_compatible is True
                and self.package_matches_baseline is not False)


def parse_executable_version(output: str) -> tuple[str, str, str]:
    match = _VERSION_RE.search(output)
    if not match:
        raise ValueError("unrecognized ppf-cts-server --version output")
    return match["package"], match["protocol"], match["schema"]


def validate_versions(protocol: str | None, schema: str | None,
                      package: str | None, *,
                      profile: ProtocolProfile | None = None,
                      ) -> CompatibilityResult:
    if profile is None and protocol is not None:
        if schema is not None:
            profile = protocol_profile(protocol, schema)
        else:
            from ..updater.solver_manifest import load_bundled_manifest
            matches = [entry for entry in load_bundled_manifest().platforms
                       if entry.protocol_version == protocol]
            if len(matches) == 1:
                entry = matches[0]
                profile = ProtocolProfile(protocol, entry.schema_version,
                                          entry.solver_package_version,
                                          entry.display_name, entry.adapter_id)
    protocol_ok = profile is not None and protocol == profile.protocol_version
    schema_ok = None if schema is None or profile is None else schema == profile.schema_version
    package_ok = (None if package is None or profile is None or profile.package_version is None
                  else package == profile.package_version)
    error = None
    if not protocol_ok or schema_ok is False or package_ok is False:
        error = ErrorRecord.create(
            category=ErrorCategory.PROTOCOL_COMPATIBILITY,
            user_message="The simulation solver is not compatible with this Cloth NeXt build.",
            technical_message=(f"expected protocol={getattr(profile, 'protocol_version', None)}, "
                               f"schema={getattr(profile, 'schema_version', None)}; "
                               f"found protocol={protocol!r}, schema={schema!r}, package={package!r}"),
            recommended_action=(
                "Install or select a solver release listed as supported by "
                "this Cloth NeXt version."),
            recoverable=False,
        )
    return CompatibilityResult(protocol, schema, package, protocol_ok, schema_ok, package_ok, error)
