"""Publication routing uses a secret; runtime updates use native repository ownership."""
import json
from types import SimpleNamespace
import pytest
from tools.release_routing import (configured_directory, publication_directories,
    repair_directory, uses_unified_repository, REPOSITORY_SECRET)
from cloth_next.updater import addon_updates as model
from cloth_next.updater.addon_versions import parse_version

FIXTURE_URL = "https://polyworks-art.github.io/Cloth_neXT/test-releases/index.json"


@pytest.mark.parametrize("version", ["2.6.1", "2.7.0", "3.0.0", "3.2.9"])
def test_all_private_release_levels_share_the_secret_destination(monkeypatch, version):
    monkeypatch.setenv(REPOSITORY_SECRET, FIXTURE_URL)
    assert publication_directories(version) == ("test-releases",)
    assert repair_directory(version, "release") == "test-releases"
    for legacy in ("stable", "beta", "dev"):
        with pytest.raises(ValueError, match="configured release repository"):
            repair_directory(version, legacy)


def test_final_bridge_does_not_need_the_secret(monkeypatch):
    monkeypatch.delenv(REPOSITORY_SECRET, raising=False)
    assert publication_directories("2.6.0") == ("beta", "dev")
    assert not uses_unified_repository("2.6.0")
    with pytest.raises(ValueError, match="must be configured"):
        publication_directories("2.7.0")


@pytest.mark.parametrize("path", ["../beta", "%2e%2e", "beta", "dev", "stable", "artifacts", "a/b", "a b"])
def test_reject_unsafe_or_legacy_destinations(path):
    with pytest.raises(ValueError):
        configured_directory(f"https://polyworks-art.github.io/Cloth_neXT/{path}/index.json")


@pytest.mark.parametrize("url", ["http://polyworks-art.github.io/Cloth_neXT/test/index.json", "https://example.com/Cloth_neXT/test/index.json", FIXTURE_URL+"?token=fixture", FIXTURE_URL+"#fragment"])
def test_no_auth_query_or_foreign_publication_url(url):
    with pytest.raises(ValueError) as error:
        configured_directory(url)
    assert url not in str(error.value)


@pytest.mark.parametrize("target,state", [("2.7.1", "UPDATE_AVAILABLE"), ("2.8.0", "UPDATE_AVAILABLE"), ("3.0.0", "UPDATE_AVAILABLE"), ("2.7.0", "UP_TO_DATE"), ("2.6.0", "UP_TO_DATE")])
def test_customer_repository_has_no_release_level_switches(target, state):
    channel = model.RepositoryChannel("https://example.com/native/index.json")
    candidates = model.parse_index_versions({"data": [{"id": "cloth_next", "version": target}]}, channel)
    decision = model.decide_update(parse_version("2.7.0"), candidates, channel)
    assert decision.state.name == state
    assert not decision.channel_changed


def test_native_repository_is_never_rewritten(blender_env, tmp_path):
    module = blender_env.addon_update_operators
    module.INSTALLED_VERSION = parse_version("2.7.0")
    module._ADDON_ID = "bl_ext.customer.cloth_next"
    owner = SimpleNamespace(module="customer", directory=str(tmp_path), enabled=True,
                            use_remote_url=True, remote_url="https://example.com/native/index.json")
    blender_env.bpy.context.preferences.extensions.repos[:] = [owner]
    before = vars(owner).copy()
    selected = module.selected_channel(blender_env.bpy.context)
    assert isinstance(selected, model.RepositoryChannel)
    assert module.prepare_repository(blender_env.bpy.context, selected) == str(tmp_path)
    assert vars(owner) == before
    assert not module.dev_access_error(blender_env.bpy.context, selected)


def test_no_configured_repository_means_no_automatic_legacy_fallback(blender_env):
    module = blender_env.addon_update_operators
    module.INSTALLED_VERSION = parse_version("2.7.0")
    selected = module.selected_channel(blender_env.bpy.context)
    assert selected.index_url == ""
    with pytest.raises(ValueError, match="Configure"):
        module.prepare_repository(blender_env.bpy.context, selected)
    assert not blender_env.bpy.context.preferences.extensions.repos
