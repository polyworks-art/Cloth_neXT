# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exact PPF baseline validation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.errors import ErrorCategory, ErrorRecord

EXPECTED_PROTOCOL = "0.11"
EXPECTED_SCHEMA = "1"
EXPECTED_PACKAGE = "0.1.0"
_VERSION_RE = re.compile(r"(?P<package>\d+\.\d+\.\d+) \(protocol v(?P<protocol>[^,]+), schema v(?P<schema>[^)]+)\)")


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


def validate_versions(protocol: str | None, schema: str | None, package: str | None) -> CompatibilityResult:
    protocol_ok = protocol == EXPECTED_PROTOCOL
    schema_ok = None if schema is None else schema == EXPECTED_SCHEMA
    package_ok = None if package is None else package == EXPECTED_PACKAGE
    error = None
    if not protocol_ok or schema_ok is False or package_ok is False:
        error = ErrorRecord.create(
            category=ErrorCategory.PROTOCOL_COMPATIBILITY,
            user_message="The PPF solver is not compatible with this Cloth NeXt build.",
            technical_message=(f"expected protocol={EXPECTED_PROTOCOL}, schema={EXPECTED_SCHEMA}; "
                               f"found protocol={protocol!r}, schema={schema!r}, package={package!r}"),
            recommended_action="Use the solver built from pinned upstream commit 7193f158.",
            recoverable=False,
        )
    return CompatibilityResult(protocol, schema, package, protocol_ok, schema_ok, package_ok, error)
