# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run inside real Blender (5.1.2): deterministic update handoff smoke test.

Verifies against Blender's real extension machinery — not a fake — that the
Cloth NeXt "Update through Blender" handoff operator:

- resolves the exact channel repository and never raises "Repository not set",
  even when a repository earlier in the list is disabled (the condition that
  shifted bl_pkg's filtered ``repo_index`` and broke the previous
  index-based implementation),
- requests synchronization only for the selected channel repository directory,
- never selects or synchronizes unrelated repositories,
- NEVER calls ``extensions.package_install`` — the self-install path that
  could make Blender replace the running extension while its code is still
  executing (the crash this hotfix removes),
- leaves the extension registered and Blender running after the handoff,
- reports a repository-disabled state distinctly,
- reports a synchronization failure distinctly,
- shows the manual Get Extensions path when the update view cannot open.

The test uses Blender's real repository preferences and the real Cloth NeXt
operator, but records the final synchronization handoff instead of contacting a
live repository. Live-network behavior is unsuitable for required CI because a
stalled remote can block Blender until the runner's six-hour limit. Repository
generation and installation are covered separately against a local repository.
The test never installs or downloads the PPF solver. Run it against an isolated
profile, e.g.:

    BLENDER_USER_RESOURCES=<tmpdir> blender --factory-startup --background \
        --online-mode --python tools/blender_update_smoke_test.py
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_updates_module():
    """Import the operators from the enabled extension or the source tree."""
    for candidate in ("bl_ext.user_default.cloth_next", "cloth_next"):
        try:
            extension = importlib.import_module(candidate)
            break
        except ModuleNotFoundError:
            continue
    else:
        sys.path.insert(0, str(REPO_ROOT))
        extension = importlib.import_module("cloth_next")
    extension.register()
    return extension, importlib.import_module(
        extension.__name__ + ".blender.addon_update_operators")


def main() -> None:
    import bpy

    extension, updates = _load_updates_module()
    from_module = updates.addon_updates
    state_cls = from_module.AddonUpdateState
    beta_url = from_module.UpdateChannel.BETA.index_url
    session = updates.session()

    # The unsafe self-install helper must stay deleted.
    assert not hasattr(updates, "_blender_package_install"), \
        "_blender_package_install returned — self-install path is forbidden"

    # The handoff operator deliberately polls false until a *newer* version is
    # actually known, so arming the session means setting `latest` as well as
    # the state. Setting only the state left `latest` at None, poll() failed,
    # bpy.ops raised, run_handoff() swallowed it as CANCELLED, and every
    # assertion below then read back the very state this fixture had just
    # written — the operator was never entered at all.
    newer_than_installed = replace(
        updates.INSTALLED_VERSION,
        patch=updates.INSTALLED_VERSION.patch + 1, stage=None, stage_number=0)

    def run_handoff():
        session.state = state_cls.UPDATE_AVAILABLE
        session.latest = newer_than_installed
        try:
            return bpy.ops.clothnext.addon_update_through_blender()
        except RuntimeError:
            # a directly called operator re-raises its ERROR report; the
            # distinct failure is already recorded in the session state
            return {"CANCELLED"}
        finally:
            assert session.state is not state_cls.UPDATE_AVAILABLE, (
                "the handoff operator was never entered — poll() rejected it, "
                "so this test would be asserting against its own fixture")

    online = bool(getattr(bpy.app, "online_access", False))
    if not online:
        # offline: blocked before any repository access, with a clear state;
        # the repository paths below all require online access
        result = run_handoff()
        assert result == {"CANCELLED"}
        assert session.state is state_cls.ONLINE_ACCESS_DISABLED, session.state
        extension.unregister()
        print("Cloth NeXt add-on update smoke test passed "
              f"(Blender {bpy.app.version_string}, offline: online gate only)")
        return

    # --- 1. no repository configured: distinct state, nothing raised ---------
    repos = bpy.context.preferences.extensions.repos
    assert from_module.find_channel_repo(repos, from_module.UpdateChannel.BETA) is None, \
        "test requires a profile without a preconfigured Cloth NeXt repository"
    run_handoff()
    assert session.state is state_cls.REPOSITORY_NOT_CONFIGURED, session.state
    assert "Repository not set" not in session.message

    # --- 2. recreate the real failure condition: a disabled repository -------
    # earlier in the list shifted bl_pkg's filtered repo_index away from the
    # raw preferences index and made the old implementation raise
    # "Repository not set".
    for repo in repos:
        if repo.module == "blender_org":
            repo.enabled = False
    bpy.ops.preferences.extension_repo_add(
        name="Cloth NeXt Beta Smoke", remote_url=beta_url, type="REMOTE")
    index = from_module.find_channel_repo(repos, from_module.UpdateChannel.BETA)
    assert index is not None, "channel repository was not found after adding it"
    channel_directory = repos[index].directory
    assert channel_directory, "repository directory RNA is empty"
    unrelated_sync_stamps = {
        repo.module: Path(repo.directory, ".blender_ext").exists()
        for repo in repos if repo.directory != channel_directory}

    # --- 3. disabled channel repository: distinct state ----------------------
    repos[index].enabled = False
    run_handoff()
    assert session.state is state_cls.REPOSITORY_DISABLED, session.state
    assert "Repository not set" not in session.message
    repos[index].enabled = True

    # --- 4. the real handoff path --------------------------------------------
    # Exercise the real operator and repository lookup while recording the
    # final sync boundary. This proves the raw/filtered repository-index bug is
    # fixed without making required CI depend on a live remote response.
    original_sync_step4 = updates._blender_repo_sync
    original_refresh_step4 = updates.refresh_update_session
    original_view_step4 = updates._blender_show_update_view
    sync_calls = []
    try:
        def record_sync(directory):
            sync_calls.append(directory)
            Path(directory, ".blender_ext").mkdir(parents=True, exist_ok=True)
        updates._blender_repo_sync = record_sync
        # Refresh behavior is covered by pure update-model tests with local
        # index payloads. Keep this real-Blender test free of remote I/O.
        updates.refresh_update_session = lambda *_args, **_kwargs: None
        updates._blender_show_update_view = lambda: None
        result = run_handoff()
        assert "Repository not set" not in session.message, session.message
        assert session.state is state_cls.READY_IN_BLENDER, (
            session.state, session.message)
        assert result == {"FINISHED"}
        assert "synchronized" in session.message.lower()
        assert sync_calls == [channel_directory]
        # honest wording: the handoff never claims an installation happened
        assert "was installed" not in session.message.lower()
    finally:
        updates._blender_repo_sync = original_sync_step4
        updates.refresh_update_session = original_refresh_step4
        updates._blender_show_update_view = original_view_step4
    # the running extension was not uninstalled, disabled, or unregistered
    assert updates.CLOTHNEXT_OT_addon_update_through_blender.is_registered

    # unrelated repositories were never selected or synchronized
    for repo in repos:
        if repo.directory != channel_directory:
            assert Path(repo.directory, ".blender_ext").exists() == \
                unrelated_sync_stamps.get(repo.module, False), (
                f"unrelated repository {repo.module} was touched")

    # --- 5. synchronization failure is a distinct, honest state --------------
    original_sync_step5 = updates._blender_repo_sync
    original_view_step5 = updates._blender_show_update_view
    views = []
    try:
        def failing_sync(_directory):
            raise RuntimeError("simulated: synchronization failed")
        updates._blender_repo_sync = failing_sync
        updates._blender_show_update_view = lambda: views.append("view")
        result = run_handoff()
        assert result == {"CANCELLED"}
        assert session.state is state_cls.SYNC_FAILED, session.state
        assert views == [], "update view opened although the sync failed"
    finally:
        updates._blender_repo_sync = original_sync_step5
        updates._blender_show_update_view = original_view_step5

    # --- 6. manual path when the update view cannot open ---------------------
    original_view = updates._blender_show_update_view
    original_sync = updates._blender_repo_sync
    original_refresh = updates.refresh_update_session
    calls = []
    try:
        updates._blender_repo_sync = lambda directory: calls.append(
            ("sync", directory))
        updates.refresh_update_session = lambda *_args, **_kwargs: None
        def unavailable():
            raise RuntimeError("simulated: update UI context unavailable")
        updates._blender_show_update_view = unavailable
        result = run_handoff()
        assert result == {"FINISHED"}
        assert session.state is state_cls.READY_IN_BLENDER, session.state
        assert "Get Extensions" in session.message
        assert beta_url in session.message
        assert calls == [("sync", channel_directory)]
    finally:
        updates._blender_show_update_view = original_view
        updates._blender_repo_sync = original_sync
        updates.refresh_update_session = original_refresh

    extension.unregister()
    print("Cloth NeXt add-on update smoke test passed "
          f"(Blender {bpy.app.version_string}, online={online})")


if __name__ == "__main__":
    main()
