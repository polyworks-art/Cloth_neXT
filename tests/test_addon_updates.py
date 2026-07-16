# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure add-on update model tests: channels, index parsing, evaluation, guard."""

import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.core.state import ApplicationState
from cloth_next.updater import addon_updates
from cloth_next.updater.addon_update_guard import (UPDATE_SAFE_STATES,
                                                   can_start_addon_update)
from cloth_next.updater.addon_updates import (AddonUpdateState, UpdateChannel,
                                              build_section_view,
                                              default_channel, evaluate_update,
                                              find_channel_repo,
                                              parse_index_versions,
                                              release_notes_url,
                                              run_update_check,
                                              validate_index_url)
from cloth_next.updater.addon_versions import parse_version

REPO_ROOT = Path(__file__).resolve().parents[1]


def index_payload(*versions, extension_id="cloth_next"):
    return {"version": "v1",
            "data": [{"id": extension_id, "version": v} for v in versions]}


def repo(url, enabled=True):
    return SimpleNamespace(remote_url=url, enabled=enabled, use_remote_url=bool(url))


# --- channels -------------------------------------------------------------------

def test_channel_urls_are_the_policy_urls():
    assert UpdateChannel.STABLE.index_url == \
        "https://polyworks-art.github.io/Cloth_neXT/stable/index.json"
    assert UpdateChannel.BETA.index_url == \
        "https://polyworks-art.github.io/Cloth_neXT/beta/index.json"
    assert UpdateChannel.DEV.index_url == \
        "https://polyworks-art.github.io/Cloth_neXT/dev/index.json"
    for channel in UpdateChannel:
        validate_index_url(channel.index_url)


def test_default_channel_follows_installed_version():
    assert default_channel(parse_version("0.2.0-beta.1")) is UpdateChannel.BETA
    assert default_channel(parse_version("0.3.0-rc.1")) is UpdateChannel.BETA
    assert default_channel(parse_version("1.0.0")) is UpdateChannel.STABLE
    assert default_channel(parse_version("0.3.21")) is UpdateChannel.DEV
    assert default_channel(parse_version("0.2.0-dev.1")) is UpdateChannel.DEV


def test_validate_index_url_rejects_foreign_hosts_and_http():
    with pytest.raises(ValueError):
        validate_index_url("http://polyworks-art.github.io/Cloth_neXT/beta/index.json")
    with pytest.raises(ValueError):
        validate_index_url("https://example.com/beta/index.json")


# --- repository lookup -----------------------------------------------------------

def test_find_channel_repo_matches_only_its_own_channel():
    repos = [repo("https://polyworks-art.github.io/Cloth_neXT/stable/index.json")]
    # Beta never treats a Stable-only repository configuration as its source.
    assert find_channel_repo(repos, UpdateChannel.BETA) is None
    assert find_channel_repo(repos, UpdateChannel.STABLE) == 0


def test_find_channel_repo_normalizes_trailing_slash():
    repos = [repo("https://polyworks-art.github.io/Cloth_neXT/beta/index.json/")]
    assert find_channel_repo(repos, UpdateChannel.BETA) == 0


def test_find_channel_repo_ignores_unrelated_repos():
    repos = [repo("https://extensions.blender.org/api/v1/extensions/"),
             repo(""), SimpleNamespace(enabled=True)]
    assert find_channel_repo(repos, UpdateChannel.BETA) is None


# --- index parsing and channel content rules --------------------------------------

def test_parse_index_versions_reads_cloth_next_entries():
    payload = index_payload("0.2.0-beta.1", "0.2.0-rc.1")
    versions = parse_index_versions(payload, UpdateChannel.BETA)
    assert [str(v) for v in versions] == ["0.2.0-beta.1", "0.2.0-rc.1"]


def test_stable_channel_never_accepts_prereleases():
    payload = index_payload("1.0.0", "0.3.0-beta.1")
    with pytest.raises(ValueError):
        parse_index_versions(payload, UpdateChannel.STABLE)


def test_beta_channel_accepts_stable_releases():
    payload = index_payload("1.0.0")
    versions = parse_index_versions(payload, UpdateChannel.BETA)
    assert tuple(map(str, versions)) == ("1.0.0",)

def test_dev_channel_accepts_dev_beta_and_stable_versions():
    for value in ("0.3.21", "0.3.0", "0.2.0-beta.7",
                  "0.2.0-rc.1", "1.0.0"):
        versions = parse_index_versions(index_payload(value), UpdateChannel.DEV)
        assert str(versions[0]) == value

def test_beta_rejects_dev_versions():
    with pytest.raises(ValueError):
        parse_index_versions(index_payload("0.3.21"),UpdateChannel.BETA)


def test_parse_index_ignores_other_extensions():
    payload = {"data": [{"id": "other_addon", "version": "9.9.9"}]}
    assert parse_index_versions(payload, UpdateChannel.STABLE) == ()


def test_parse_index_rejects_malformed_payloads():
    with pytest.raises(ValueError):
        parse_index_versions({"data": "nope"}, UpdateChannel.BETA)
    with pytest.raises(ValueError):
        parse_index_versions({}, UpdateChannel.BETA)
    with pytest.raises(ValueError):  # malformed version string inside
        parse_index_versions(index_payload("latest"), UpdateChannel.BETA)


# --- evaluation and state mapping ---------------------------------------------------

def test_evaluate_update_states():
    installed = parse_version("0.2.0-beta.1")
    assert evaluate_update(installed, ()) == (AddonUpdateState.UNAVAILABLE, None)
    state, latest = evaluate_update(installed,
                                    (parse_version("0.2.0-beta.2"),
                                     parse_version("0.2.0-beta.1")))
    assert state is AddonUpdateState.UPDATE_AVAILABLE
    assert str(latest) == "0.2.0-beta.2"
    state, latest = evaluate_update(installed, (parse_version("0.2.0-beta.1"),))
    assert state is AddonUpdateState.UP_TO_DATE


def test_no_downgrade_across_required_beta_and_dev_regressions():
    cases = [
        ("0.3.0-beta.1", ("0.2.0-beta.5",), AddonUpdateState.UP_TO_DATE),
        ("0.3.0-beta.1", ("0.3.0-beta.2",), AddonUpdateState.UPDATE_AVAILABLE),
        ("0.3.0-beta.2", ("0.3.0-beta.1",), AddonUpdateState.UP_TO_DATE),
        ("0.3.0-dev.9", ("0.2.0-beta.5", "0.3.0-dev.1"),
         AddonUpdateState.UP_TO_DATE),
    ]
    for installed, available, expected in cases:
        state, _latest = evaluate_update(
            parse_version(installed), tuple(map(parse_version, available)))
        assert state is expected


def test_ambiguous_and_archive_mismatched_index_is_visible_error():
    duplicate = {"data": [
        {"id": "cloth_next", "version": "0.3.0-beta.2"},
        {"id": "cloth_next", "version": "0.3.0-beta.2"},
    ]}
    with pytest.raises(ValueError, match="ambiguous"):
        parse_index_versions(duplicate, UpdateChannel.BETA)
    mismatch = {"data": [{"id": "cloth_next", "version": "0.3.0-beta.2",
                           "archive_url": "./cloth_next-0.2.0-beta.5.zip"}]}
    with pytest.raises(ValueError, match="mismatch"):
        parse_index_versions(mismatch, UpdateChannel.BETA)


def test_run_update_check_success_and_error_paths():
    session = addon_updates.AddonUpdateSession()
    run_update_check(session, UpdateChannel.BETA, parse_version("0.1.0"),
                     fetch=lambda _c: index_payload("0.2.0-beta.1"))
    assert session.state is AddonUpdateState.UPDATE_AVAILABLE
    assert str(session.latest) == "0.2.0-beta.1"

    def failing_fetch(_channel):
        raise OSError("connection timed out")

    run_update_check(session, UpdateChannel.BETA, parse_version("0.1.0"),
                     fetch=failing_fetch)
    assert session.state is AddonUpdateState.ERROR
    assert "timed out" in session.message
    assert session.latest is None


# --- current version from the canonical manifest source -----------------------------

def test_installed_version_comes_from_the_manifest():
    import cloth_next
    manifest = tomllib.loads(
        (REPO_ROOT / "cloth_next" / "blender_manifest.toml").read_text("utf-8"))
    assert str(parse_version(cloth_next.manifest_version())) == manifest["version"]


# --- release notes -------------------------------------------------------------------

def test_release_notes_url():
    assert release_notes_url(None) == \
        "https://github.com/polyworks-art/Cloth_neXT/releases"
    assert release_notes_url(parse_version("0.2.0-beta.1")) == \
        "https://github.com/polyworks-art/Cloth_neXT/releases/tag/v0.2.0-beta.1"


# --- section view --------------------------------------------------------------------

def test_section_view_state_mapping():
    view = build_section_view(AddonUpdateState.UPDATE_AVAILABLE,
                              parse_version("0.2.0-beta.1"), "")
    assert view.show_update_handoff and not view.show_open_extensions
    assert "0.2.0-beta.1" in view.status_text
    # honest wording: the update completes in Blender, not in Cloth NeXt
    assert "native extension manager" in view.message
    assert "0.2.0-beta.1 is available" in view.message

    view = build_section_view(AddonUpdateState.UP_TO_DATE, None, "")
    assert not view.show_update_handoff and view.show_open_extensions

    view = build_section_view(AddonUpdateState.READY_IN_BLENDER, None, "m")
    # opening the update view proves no installation: never claim one
    assert "install" not in view.status_text.lower()
    assert "restart" not in view.status_text.lower()

    view = build_section_view(AddonUpdateState.REPOSITORY_NOT_CONFIGURED, None, "x")
    assert view.show_repo_setup and view.message == "x"

    view = build_section_view(AddonUpdateState.CHECKING, None, "")
    assert not view.check_enabled

    for state in AddonUpdateState:
        assert build_section_view(state, None, "").status_text


# --- update guard (items 15+16) -------------------------------------------------------

def test_update_guard_allows_only_documented_safe_states():
    assert UPDATE_SAFE_STATES == {ApplicationState.NOT_INSTALLED,
                                  ApplicationState.STOPPED,
                                  ApplicationState.ERROR}
    for state in ApplicationState:
        expected = state in UPDATE_SAFE_STATES
        assert can_start_addon_update(state) is expected, state


def test_update_guard_blocks_every_unsafe_state():
    unsafe = [s for s in ApplicationState if s not in UPDATE_SAFE_STATES]
    assert unsafe  # the state machine has blocking states
    for state in unsafe:
        assert not can_start_addon_update(state)
