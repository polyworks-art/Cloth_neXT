# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persistent registry for side-by-side managed and external solvers."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

REGISTRY_VERSION = 1


@dataclass(frozen=True, slots=True)
class SolverInstallation:
    installation_id: str
    display_name: str
    source: str
    root_path: str
    executable_path: str
    frontend_path: str | None
    package_version: str | None
    protocol_version: str | None
    schema_version: str | None
    official_release_tag: str | None
    managed: bool
    verified: bool
    healthy: bool
    channel: str = "unsupported"
    error: str | None = None

    @property
    def root(self) -> Path:
        return Path(self.root_path)

    @property
    def executable(self) -> Path:
        return Path(self.executable_path)

    @property
    def frontend(self) -> Path | None:
        return Path(self.frontend_path) if self.frontend_path else None

    @property
    def compatible(self) -> bool:
        from ..ppf.compatibility import protocol_profile
        return bool(
            self.protocol_version and self.schema_version
            and protocol_profile(self.protocol_version, self.schema_version))

    @property
    def available(self) -> bool:
        return self.root.is_dir() and self.executable.is_file()


@dataclass(frozen=True, slots=True)
class SolverRegistry:
    installations: tuple[SolverInstallation, ...] = ()
    selected_installation_id: str | None = None

    def get(self, installation_id: str | None) -> SolverInstallation | None:
        return next((item for item in self.installations
                     if item.installation_id == installation_id), None)

    def find_executable(self, executable: Path) -> SolverInstallation | None:
        """Return an existing registration for the same resolved executable."""
        try:
            target = executable.resolve()
        except OSError:
            target = executable.absolute()
        for item in self.installations:
            try:
                candidate = item.executable.resolve()
            except OSError:
                candidate = item.executable.absolute()
            if candidate == target:
                return item
        return None

    @property
    def selected(self) -> SolverInstallation | None:
        return self.get(self.selected_installation_id)

    def register(self, installation: SolverInstallation) -> "SolverRegistry":
        existing = self.get(installation.installation_id)
        if existing is not None and existing != installation:
            raise ValueError(
                f"installation id {installation.installation_id!r} is already registered")
        if existing is not None:
            return self
        return replace(
            self, installations=self.installations + (installation,))

    def unregister(self, installation_id: str) -> "SolverRegistry":
        return replace(
            self,
            installations=tuple(item for item in self.installations
                                if item.installation_id != installation_id),
            selected_installation_id=(
                None if self.selected_installation_id == installation_id
                else self.selected_installation_id))

    def update(self, installation: SolverInstallation) -> "SolverRegistry":
        if self.get(installation.installation_id) is None:
            raise ValueError(
                f"unknown solver installation {installation.installation_id!r}")
        return replace(
            self,
            installations=tuple(
                installation if item.installation_id
                == installation.installation_id else item
                for item in self.installations))

    def select(self, installation_id: str | None) -> "SolverRegistry":
        if installation_id is not None:
            installation = self.get(installation_id)
            if installation is None:
                raise ValueError(f"unknown solver installation {installation_id!r}")
            if not (installation.compatible and installation.verified
                    and installation.healthy and installation.available):
                raise ValueError("only a healthy verified compatible installation "
                                 "can be selected")
        return replace(self, selected_installation_id=installation_id)


def official_installation_id(release_tag: str,
                             platform_suffix: str = "win64") -> str:
    safe = release_tag.strip().replace("_", "-")
    if not safe or any(value in safe for value in ("/", "\\", "..")):
        raise ValueError(f"invalid official release tag {release_tag!r}")
    return f"official-{safe}-{platform_suffix}"


def external_installation_id() -> str:
    return f"external-{uuid.uuid4()}"


def _parse_installation(payload: object) -> SolverInstallation:
    if not isinstance(payload, dict):
        raise ValueError("registry installation must be an object")
    allowed = {field.name for field in SolverInstallation.__dataclass_fields__.values()}
    try:
        installation = SolverInstallation(**{
            key: value for key, value in payload.items() if key in allowed})
    except TypeError as exc:
        raise ValueError(f"invalid registry installation: {exc}") from exc
    required_strings = (
        installation.installation_id, installation.display_name,
        installation.source, installation.root_path,
        installation.executable_path)
    if not all(isinstance(value, str) and value for value in required_strings):
        raise ValueError("registry installation has missing string fields")
    if not all(isinstance(value, bool) for value in (
            installation.managed, installation.verified,
            installation.healthy)):
        raise ValueError("registry installation has invalid state fields")
    return installation


def load_registry(path: Path) -> SolverRegistry:
    if not path.is_file():
        return SolverRegistry()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"solver registry is corrupt: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("registry_version") != REGISTRY_VERSION:
        raise ValueError("solver registry has an unsupported format")
    raw = payload.get("installations")
    if not isinstance(raw, list):
        raise ValueError("solver registry installations must be a list")
    installations = tuple(_parse_installation(item) for item in raw)
    ids = tuple(item.installation_id for item in installations)
    if len(ids) != len(set(ids)):
        raise ValueError("solver registry contains duplicate installation ids")
    selected = payload.get("selected_installation_id")
    if selected is not None and not isinstance(selected, str):
        raise ValueError("selected_installation_id must be a string or null")
    return SolverRegistry(installations, selected)


def write_registry(path: Path, registry: SolverRegistry) -> SolverRegistry:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "registry_version": REGISTRY_VERSION,
        "selected_installation_id": registry.selected_installation_id,
        "installations": [asdict(item) for item in registry.installations],
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return registry


def migrate_legacy_current(paths, manifest, *,
                           probe_version,
                           health_check) -> SolverRegistry:
    """Idempotently register the existing single managed installation."""
    from ..ppf.layout import BundledSolverLayout
    from .install_paths import read_current

    try:
        registry = load_registry(paths.registry_json)
    except ValueError:
        corrupt = paths.registry_json.with_suffix(
            f".corrupt-{uuid.uuid4().hex}.json")
        os.replace(paths.registry_json, corrupt)
        registry = SolverRegistry()
    active = read_current(paths)
    if active is None:
        return write_registry(paths.registry_json, registry)
    root = paths.version_dir(active.installation_id)
    executable = active.executable_path(paths)
    if not executable.is_file():
        return write_registry(paths.registry_json, registry)
    package, protocol, schema = probe_version(executable)
    release = next((
        entry for entry in manifest.releases_for("windows-x86_64")
        if entry.official_release_tag == active.official_release_tag
        or (active.official_release_tag is None
            and entry.solver_package_version == package
            and entry.protocol_version == protocol
            and entry.schema_version == schema)), None)
    known = bool(release and active.has_release_identity)
    installation_id = (
        official_installation_id(release.official_release_tag)
        if known and release is not None
        else f"legacy-{uuid.uuid5(uuid.NAMESPACE_URL, str(root.resolve()))}")
    if registry.get(installation_id) is None:
        layout = BundledSolverLayout.from_root(root)
        installation = SolverInstallation(
            installation_id=installation_id,
            display_name=(release.display_name if known and release
                          else "Legacy PPF Contact Solver"),
            source="official" if known else "legacy",
            root_path=str(root.resolve()),
            executable_path=str(executable.resolve()),
            frontend_path=str((layout.root_directory / "frontend").resolve()),
            package_version=package,
            protocol_version=protocol,
            schema_version=schema,
            official_release_tag=(
                release.official_release_tag if known and release else None),
            managed=known,
            verified=True,
            healthy=bool(health_check(executable)),
            channel=(release.channel if known and release else "unsupported"),
            error=None)
        registry = registry.register(installation)
    if registry.selected_installation_id is None:
        registry = replace(registry, selected_installation_id=installation_id)
    return write_registry(paths.registry_json, registry)
