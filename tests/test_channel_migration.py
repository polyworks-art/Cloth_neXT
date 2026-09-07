"""Bridge migration and all six channel handoffs against fake Blender RNA."""
import json
from types import SimpleNamespace

import pytest

from cloth_next.updater import addon_updates as model
from cloth_next.updater.addon_versions import parse_version

VERSIONS = {"STABLE": "2.0.0", "BETA": "2.3.0", "DEV": "2.3.5"}


@pytest.mark.parametrize("source", VERSIONS)
@pytest.mark.parametrize("destination", VERSIONS)
def test_channel_decision_matrix(source, destination):
    installed = parse_version(VERSIONS[source])
    target = parse_version(VERSIONS[destination])
    decision = model.decide_update(installed, (target,), model.UpdateChannel[destination])
    assert decision.installed_channel == source.lower()
    assert decision.target_channel == destination.lower()
    assert decision.same_artifact == (source == destination)
    assert decision.channel_changed == (source != destination)
    assert decision.state is (model.AddonUpdateState.UP_TO_DATE if source == destination
                              else model.AddonUpdateState.SWITCH_CHANNEL)
    assert decision.version_relationship == (
        "equal" if target == installed else "older" if target < installed else "newer")


@pytest.mark.parametrize("target,state", [("2.3.4", "UP_TO_DATE"),
    ("2.3.5", "UP_TO_DATE"), ("2.3.6", "UPDATE_AVAILABLE")])
def test_same_channel_relationship(target, state):
    decision = model.decide_update(parse_version("2.3.5"), (parse_version(target),),
                                   model.UpdateChannel.DEV)
    assert decision.state.name == state


def installation(env, tmp_path, source, destination):
    module = env.addon_update_operators
    module._ADDON_ID = "bl_ext.legacy.cloth_next"
    module.INSTALLED_VERSION = parse_version(VERSIONS[source])
    prefs = SimpleNamespace(update_channel=destination, developer_tools=False,
                            dev_channel_acknowledged=True, license="preserved")
    env.bpy.context.preferences.addons["cloth_next"] = SimpleNamespace(preferences=prefs)
    owner = SimpleNamespace(module="legacy", directory=str(tmp_path), enabled=True,
                            remote_url=model.UpdateChannel[source].index_url)
    duplicate = SimpleNamespace(module="duplicate", directory="old", enabled=True,
                                remote_url=model.UpdateChannel[destination].index_url)
    env.bpy.context.preferences.extensions.repos[:] = [duplicate, owner]
    cache = tmp_path / ".blender_ext"
    cache.mkdir(exist_ok=True)
    def sync(directory):
        assert directory == str(tmp_path)
        assert owner.remote_url == model.UpdateChannel[destination].index_url
        (cache / "index.json").write_text(json.dumps({"data": [
            {"id": "cloth_next", "version": VERSIONS[destination]}]}))
    module._blender_repo_sync = sync
    return module, owner, duplicate, prefs


@pytest.mark.parametrize("source", VERSIONS)
@pytest.mark.parametrize("destination", VERSIONS)
def test_bridge_and_handoff_same_owner(blender_env, tmp_path, source, destination):
    module, owner, duplicate, prefs = installation(blender_env, tmp_path, source, destination)
    context = blender_env.bpy.context
    identity = (owner.module, owner.directory)
    preferences_before = vars(prefs).copy()
    duplicate_before = vars(duplicate).copy()
    module.initialize_updates()
    assert module._automatic_update_check_timer in blender_env.bpy.app.timers.functions
    module._automatic_update_check_timer()
    assert module.session().migrated_channel is model.UpdateChannel[destination]
    assert (owner.module, owner.directory) == identity
    assert vars(prefs) == preferences_before
    assert vars(duplicate) == duplicate_before
    assert len(context.preferences.extensions.repos) == 2
    assert module.synchronize_selected(context, model.UpdateChannel[destination])
    if source != destination:
        assert module.CLOTHNEXT_OT_addon_update_through_blender.poll(context)
        view = model.build_section_view(module.session().state, module.session().latest, "")
        assert view.show_update_handoff
        assert module.CLOTHNEXT_OT_addon_update_through_blender().execute(context) == {"FINISHED"}
        assert any(name == "extensions.userpref_show_for_update" for name, _ in blender_env.bpy.ops_log)
    assert not any(name in {"extensions.package_install", "preferences.extension_repo_add"}
                   for name, _ in blender_env.bpy.ops_log)


@pytest.mark.parametrize("failure", ["unavailable", "invalid", "wrong_channel", "cancelled"])
def test_partial_migration_retries_without_touching_installation(blender_env, tmp_path, failure):
    module, owner, duplicate, prefs = installation(blender_env, tmp_path, "DEV", "STABLE")
    good_sync = module._blender_repo_sync
    installed = tmp_path / "cloth_next"
    installed.mkdir()
    sentinel = installed / "license_state"
    sentinel.write_text("unchanged")
    def failing_sync(directory):
        if failure == "unavailable":
            raise OSError("offline")
        if failure == "cancelled":
            raise RuntimeError("sync cancelled")
        (tmp_path / ".blender_ext" / "index.json").write_text(
            "invalid" if failure == "invalid" else
            json.dumps({"data": [{"id": "cloth_next", "version": "2.3.5"}]}))
    module._blender_repo_sync = failing_sync
    assert not module.synchronize_selected(blender_env.bpy.context, model.UpdateChannel.STABLE)
    assert module.session().migrated_channel is None
    assert owner.remote_url == model.UpdateChannel.STABLE.index_url
    assert sentinel.read_text() == "unchanged"
    module._blender_repo_sync = good_sync
    assert module.synchronize_selected(blender_env.bpy.context, model.UpdateChannel.STABLE)
    assert module.session().migrated_channel is model.UpdateChannel.STABLE
    assert sentinel.read_text() == "unchanged"


def test_channel_change_discards_stale_action(blender_env, tmp_path):
    module, owner, _, prefs = installation(blender_env, tmp_path, "STABLE", "DEV")
    assert module.synchronize_selected(blender_env.bpy.context, model.UpdateChannel.DEV)
    old_session = module.session()
    prefs.update_channel = "BETA"
    module.channel_changed(prefs, blender_env.bpy.context)
    assert module.session() is not old_session
    assert not module.CLOTHNEXT_OT_addon_update_through_blender.poll(blender_env.bpy.context)


def test_ambiguous_owner_and_url_only_match_are_rejected():
    repos = [SimpleNamespace(module="legacy", remote_url=model.UpdateChannel.DEV.index_url)]
    assert model.find_owning_repo(repos, "cloth_next") is None
    assert model.find_owning_repo(repos, "bl_ext.other.cloth_next") is None
    assert model.find_owning_repo(repos * 2, "bl_ext.legacy.cloth_next") is None


def test_existing_dev_migrates_without_new_acknowledgement(blender_env, tmp_path):
    module, _, _, prefs = installation(blender_env, tmp_path, "DEV", "DEV")
    prefs.dev_channel_acknowledged = False
    module.initialize_updates()
    module._automatic_update_check_timer()
    assert module.session().migrated_channel is model.UpdateChannel.DEV


@pytest.mark.parametrize("target", ["2.3.4", "2.3.5"])
def test_poll_rejects_stale_same_channel_action(blender_env, tmp_path, target):
    module, _, _, _ = installation(blender_env, tmp_path, "DEV", "DEV")
    module.session().state = model.AddonUpdateState.UPDATE_AVAILABLE
    module.session().latest = parse_version(target)
    assert not module.CLOTHNEXT_OT_addon_update_through_blender.poll(blender_env.bpy.context)


def test_source_identity_uses_actual_directory(tmp_path):
    repo = SimpleNamespace(module="local", directory=str(tmp_path))
    assert model.find_owning_repo([repo], "cloth_next", tmp_path / "cloth_next") == 0
    assert model.find_owning_repo([repo], "cloth_next", tmp_path / "other") is None
