# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Static Welcome, validated What's-New content, and durable seen-state logic."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

WHATS_NEW_SCHEMA = "cnx.whats-new.v1"
SUPPORTED_ACTIONS = frozenset({"close", "url"})
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ICON_ASSETS = frozenset(
    f"icons/{name}.png" for name in (
        "arrow", "changelog", "check", "close", "cloth", "docs", "link",
        "logo", "play", "refresh", "rig", "rocket", "search", "settings",
        "shield", "sliders"))


def default_resource_root() -> Path:
    return Path(__file__).resolve().parent / "resources" / "onboarding"


def _required_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _safe_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"invalid public HTTPS URL: {value!r}")
    return value


def _safe_asset(root: Path, value: str, asset_exists=None) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or re.match(r"^[A-Za-z]:", value):
        raise ValueError(f"asset path must be package-relative: {value!r}")
    exists = (asset_exists(value) if asset_exists is not None
              else (root / Path(*path.parts)).is_file())
    if not exists:
        raise ValueError(f"referenced asset does not exist: {value}")
    return value


def _safe_icon(root: Path, value: str, asset_exists=None) -> str:
    icon = _safe_asset(root, value, asset_exists)
    if icon not in ICON_ASSETS:
        raise ValueError(f"icon is not in the approved onboarding icon pool: {icon}")
    return icon


def _validate_actions(payload: dict) -> tuple[dict, ...]:
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("actions must be a non-empty list")
    validated = []
    for item in actions:
        if not isinstance(item, dict):
            raise ValueError("each action must be an object")
        kind = _required_text(item, "kind")
        if kind not in SUPPORTED_ACTIONS:
            raise ValueError(f"unsupported action kind: {kind}")
        action = {"label": _required_text(item, "label"), "kind": kind}
        if kind == "url":
            action["url"] = _safe_url(_required_text(item, "url"))
        validated.append(action)
    return tuple(validated)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"resource is not valid UTF-8: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"resource is not valid JSON: {path.name}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"resource root must be an object: {path.name}")
    return payload


WELCOME_ASSETS = (
    "assets/hero-panel.png", "icons/link.png", "icons/cloth.png", "icons/play.png")
WELCOME_CONTENT = {
    "title": "Welcome to Cloth NeXt",
    "subtitle": "GPU-powered cloth simulation shaped for an artist-friendly Blender workflow.",
    "hero_asset": WELCOME_ASSETS[0],
    "steps": (
        {"title": "Connect your solver", "description":
         "Install or select the external PPF Contact Solver in Cloth NeXt Preferences. "
         "It is separate software and is never bundled with Cloth NeXt."},
        {"title": "Prepare your cloth", "description":
         "Choose your cloth and collider objects, then tune material, collision, and "
         "pinning controls in Blender."},
        {"title": "Start your first bake", "description":
         "Run Bake from the Cloth NeXt Cache panel and follow progress in the Companion."},
    ),
    "actions": (
        {"label": "Get Started", "kind": "close"},
        {"label": "Documentation", "kind": "url",
         "url": "https://polyworks-art.github.io/Cloth_neXT/superhive/docs/"},
        {"label": "Solver Setup", "kind": "url",
         "url": "https://github.com/polyworks-art/Cloth_neXT#2-install-or-select-the-solver"},
    ),
}


def load_welcome(resource_root: Path | None = None, asset_exists=None) -> dict:
    """Return invariant product onboarding after validating its packaged artwork."""
    root = resource_root or default_resource_root()
    for asset in WELCOME_ASSETS:
        _safe_asset(root, asset, asset_exists)
    return WELCOME_CONTENT


def load_whats_new(version: str, resource_root: Path | None = None) -> dict:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid What's-New version: {version!r}")
    root = resource_root or default_resource_root()
    return validate_whats_new_payload(
        _read_json(root / "whats_new" / f"{version}.json"), version, root)


def validate_whats_new_payload(payload: dict, version: str,
                               resource_root: Path, asset_exists=None) -> dict:
    root = resource_root
    if payload.get("schema") != WHATS_NEW_SCHEMA:
        raise ValueError("invalid What's-New resource schema")
    if payload.get("version") != version:
        raise ValueError("What's-New resource version mismatch")
    highlights = payload.get("highlights")
    if not isinstance(highlights, list) or not 2 <= len(highlights) <= 4:
        raise ValueError("What's New must contain two to four highlights")
    normalized_highlights = []
    for item in highlights:
        if not isinstance(item, dict):
            raise ValueError("each highlight must be an object")
        description = item.get("description", "")
        if not isinstance(description, str):
            raise ValueError("highlight description must be a string")
        icon = _required_text(item, "icon")
        icon = _safe_icon(root, icon, asset_exists)
        normalized_highlights.append({"title": _required_text(item, "title"),
                                      "description": description.strip(),
                                      "icon": icon})
    def change_list(key: str) -> tuple[dict, ...]:
        values = payload.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a list")
        changes = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError(f"each {key} item must define text and icon")
            changes.append({"text": _required_text(value, "text"),
                            "icon": _safe_icon(
                                root, _required_text(value, "icon"), asset_exists)})
        return tuple(changes)
    result = {"schema": WHATS_NEW_SCHEMA, "version": version,
              "title": _required_text(payload, "title"),
              "subtitle": _required_text(payload, "subtitle"),
              "highlights": tuple(normalized_highlights),
              "improvements": change_list("improvements"),
              "fixes": change_list("fixes"), "actions": _validate_actions(payload)}
    asset = payload.get("hero_asset")
    if asset is not None:
        if not isinstance(asset, str):
            raise ValueError("hero_asset must be a string")
        result["hero_asset"] = _safe_asset(root, asset, asset_exists)
    return result


def validate_release_content(resource_root: Path, version: str) -> None:
    load_welcome(resource_root)
    load_whats_new(version, resource_root)


def version_key(version: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError(f"invalid Cloth NeXt version: {version!r}")
    return tuple(int(value) for value in match.groups())


@dataclass(frozen=True, slots=True)
class SeenState:
    welcome_seen: bool = False
    seen_versions: tuple[str, ...] = ()
    highest_version: str = ""

    @classmethod
    def from_json(cls, text: str) -> "SeenState":
        if not text.strip():
            return cls()
        try:
            payload = json.loads(text)
            versions = tuple(v for v in payload.get("seen_versions", ())
                             if isinstance(v, str) and VERSION_RE.fullmatch(v))
            highest = payload.get("highest_version", "")
            if highest and not VERSION_RE.fullmatch(highest):
                highest = ""
            return cls(bool(payload.get("welcome_seen", False)), versions, highest)
        except (json.JSONDecodeError, TypeError, AttributeError):
            return cls()

    def to_json(self) -> str:
        return json.dumps({"schema": 1, "welcome_seen": self.welcome_seen,
                           "seen_versions": list(self.seen_versions),
                           "highest_version": self.highest_version},
                          separators=(",", ":"), sort_keys=True)

    def next_screen(self, current_version: str) -> str | None:
        current = version_key(current_version)
        if not self.welcome_seen:
            return "welcome"
        if current_version in self.seen_versions:
            return None
        if self.highest_version and current <= version_key(self.highest_version):
            return None
        return "whats-new"

    def mark_seen(self, screen: str, current_version: str) -> "SeenState":
        version_key(current_version)
        versions = tuple(dict.fromkeys((*self.seen_versions, current_version)))[-24:]
        highest = self.highest_version
        if not highest or version_key(current_version) > version_key(highest):
            highest = current_version
        return SeenState(True if screen == "welcome" else self.welcome_seen,
                         versions, highest)
