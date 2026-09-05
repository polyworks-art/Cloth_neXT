# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import json

import pytest

from cloth_next.onboarding import (SeenState, load_welcome, load_whats_new,
                                    validate_release_content)


def test_repository_welcome_and_current_whats_new_are_valid():
    welcome = load_welcome()
    current = load_whats_new("2.3.7")
    assert len(welcome["steps"]) == 3
    assert welcome["title"] == "Welcome to Cloth NeXt"
    assert current["version"] == "2.3.7"
    assert 2 <= len(current["highlights"]) <= 4


def test_fresh_install_shows_welcome_only_once_and_marks_current_seen():
    state = SeenState()
    assert state.next_screen("2.3.5") == "welcome"
    state = state.mark_seen("welcome", "2.3.5")
    assert state.next_screen("2.3.5") is None
    assert SeenState.from_json(state.to_json()) == state


def test_update_same_version_restart_and_downgrade_rules():
    state = SeenState(True, ("2.3.4",), "2.3.4")
    assert state.next_screen("2.3.5") == "whats-new"
    state = state.mark_seen("whats-new", "2.3.5")
    assert state.next_screen("2.3.5") is None
    assert SeenState.from_json(state.to_json()).next_screen("2.3.5") is None
    assert state.next_screen("2.3.4") is None


def test_channel_switches_follow_monotonic_three_counter_versions():
    stable = SeenState(True, ("2.0.0",), "2.0.0")
    assert stable.next_screen("2.1.0") == "whats-new"
    beta = stable.mark_seen("whats-new", "2.1.0")
    assert beta.next_screen("2.1.1") == "whats-new"
    dev = beta.mark_seen("whats-new", "2.1.1")
    assert dev.next_screen("2.1.0") is None
    assert dev.next_screen("3.0.0") == "whats-new"


def _write_resources(root, version="1.2.3"):
    (root / "whats_new").mkdir(parents=True)
    (root / "icons").mkdir()
    (root / "assets").mkdir()
    (root / "assets" / "hero-panel.png").write_bytes(b"png")
    for name in ("rocket.png", "shield.png", "link.png", "cloth.png", "play.png"):
        (root / "icons" / name).write_bytes(b"png")
    whats = {"schema": "cnx.whats-new.v1", "version": version,
        "title": "New", "subtitle": "Better",
        "highlights": [{"title": "A", "description": "A", "icon": "icons/rocket.png"},
                       {"title": "B", "description": "B", "icon": "icons/shield.png"}],
        "actions": [{"label": "Docs", "kind": "url",
                     "url": "https://example.com/docs"}]}
    (root / "whats_new" / f"{version}.json").write_text(
        json.dumps(whats), encoding="utf-8")
    return whats


def test_unknown_fields_are_ignored_and_optional_lists_default_empty(tmp_path):
    whats = _write_resources(tmp_path)
    whats["future_field"] = 42
    (tmp_path / "whats_new" / "1.2.3.json").write_text(
        json.dumps(whats), encoding="utf-8")
    validate_release_content(tmp_path, "1.2.3")


@pytest.mark.parametrize("mutation,match", [
    (lambda whats: whats.update(version="9.9.9"), "version mismatch"),
    (lambda whats: whats["actions"][0].update(url="file:///secret"),
     "HTTPS URL"),
])
def test_broken_content_fails_closed(tmp_path, mutation, match):
    whats = _write_resources(tmp_path)
    mutation(whats)
    (tmp_path / "whats_new" / "1.2.3.json").write_text(
        json.dumps(whats), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        validate_release_content(tmp_path, "1.2.3")


def test_corrupt_state_recovers_as_fresh_install():
    assert SeenState.from_json("{broken").next_screen("2.3.5") == "welcome"
