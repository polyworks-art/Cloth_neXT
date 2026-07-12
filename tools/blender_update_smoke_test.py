# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run inside real Blender (5.1.2): add-on update path smoke test.

Verifies against Blender's real extension machinery — not a fake — that the
Cloth NeXt update operator:

- resolves the exact channel repository and never raises "Repository not set",
  even when a repository earlier in the list is disabled (the condition that
  shifted bl_pkg's filtered ``repo_index`` and broke the previous
  index-based implementation),
- synchronizes only the selected channel repository (by directory),
- never selects or synchronizes unrelated repositories,
- reports a repository-disabled state distinctly,
- reports a synchronization failure distinctly when offline,
- falls back to Blender's extension update view path when the automatic
  invocation is unavailable.

The test never installs or downloads the PPF solver; it only talks to
Blender's extension system about the Cloth NeXt package. Run it against an
isolated profile, e.g.:

    BLENDER_USER_RESOURCES=<tmpdir> blender --factory-startup --background \
        --online-mode --python tools/blender_update_smoke_test.py
"""

from __future__ import annotations

import importlib
import sys
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

    def run_install():
        session.state = state_cls.UPDATE_AVAILABLE
        try:
            return bpy.ops.clothnext.addon_update_install()
        except RuntimeError:
            # a directly called operator re-raises its ERROR report; the
            # distinct failure is already recorded in the session state
            return {"CANCELLED"}

    online = bool(getattr(bpy.app, "online_access", False))
    if not online:
        # offline: blocked before any repository access, with a clear state;
        # the repository paths below all require online access
        result = run_install()
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
    run_install()
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
    run_install()
    assert session.state is state_cls.REPOSITORY_DISABLED, session.state
    assert "Repository not set" not in session.message
    repos[index].enabled = True

    # --- 4. the real update path -------------------------------------------
    # Production installs the update over the enabled extension in the same
    # repository, which bl_pkg disables and re-enables around the install. In
    # this test the running copy comes from the source tree instead, so
    # enabling the freshly installed second copy would double-register the
    # same bl_idnames. Keep Blender's real operator, sync, download, and
    # install, but skip only the enable step of this artificial setup.
    production_install = updates._blender_package_install
    updates._blender_package_install = (
        lambda directory: bpy.ops.extensions.package_install(
            repo_directory=directory, pkg_id=from_module.EXTENSION_ID,
            enable_on_install=False))
    try:
        result = run_install()
    finally:
        updates._blender_package_install = production_install
    assert "Repository not set" not in session.message, session.message
    assert session.state is state_cls.RESTART_REQUIRED, (
        session.state, session.message)
    assert result == {"FINISHED"}
    # the exact repository was synchronized (Blender wrote its extension
    # cache into the channel repository directory)
    assert Path(channel_directory, ".blender_ext").exists(), \
        "channel repository was not synchronized"

    # unrelated repositories were never selected or synchronized
    for repo in repos:
        if repo.directory != channel_directory:
            assert Path(repo.directory, ".blender_ext").exists() == \
                unrelated_sync_stamps.get(repo.module, False), (
                f"unrelated repository {repo.module} was touched")

    # --- 5. synchronization failure is a distinct, honest state --------------
    original_sync_step5 = updates._blender_repo_sync
    installs = []
    original_install_step5 = updates._blender_package_install
    try:
        def failing_sync(_directory):
            raise RuntimeError("simulated: synchronization failed")
        updates._blender_repo_sync = failing_sync
        updates._blender_package_install = lambda directory: installs.append(directory)
        result = run_install()
        assert result == {"CANCELLED"}
        assert session.state is state_cls.SYNC_FAILED, session.state
        assert installs == [], "install ran although synchronization failed"
    finally:
        updates._blender_repo_sync = original_sync_step5
        updates._blender_package_install = original_install_step5

    # --- 6. fallback path when the automatic call is unavailable -------------
    original_install = updates._blender_package_install
    original_sync = updates._blender_repo_sync
    calls = []
    try:
        updates._blender_repo_sync = lambda directory: calls.append(
            ("sync", directory))
        def unavailable(_directory):
            raise RuntimeError("simulated: update UI context unavailable")
        updates._blender_package_install = unavailable
        result = run_install()
        assert result == {"FINISHED"}
        assert session.state is state_cls.UNAVAILABLE, session.state
        assert "synchronized" in session.message.lower()
        assert "click" in session.message.lower()
        assert calls == [("sync", channel_directory)]
    finally:
        updates._blender_package_install = original_install
        updates._blender_repo_sync = original_sync

    extension.unregister()
    print("Cloth NeXt add-on update smoke test passed "
          f"(Blender {bpy.app.version_string}, online={online})")


if __name__ == "__main__":
    main()
