"""Regression gates for the final GitHub Beta migration bridge."""
from pathlib import Path
import pytest
import tomllib

CURRENT_VERSION = tomllib.loads((Path(__file__).resolve().parents[1] / "cloth_next/blender_manifest.toml").read_text(encoding="utf-8"))["version"]
MIGRATION_ONLY = pytest.mark.skipif(CURRENT_VERSION != "2.6.0", reason="2.6.0 migration artifact only")
from cloth_next.onboarding import SeenState, load_whats_new
from tools.validate_release_policy import check_beta_bridge_target


def test_beta_bridge_rejects_advance_and_rollback_but_dev_remains_available():
    check_beta_bridge_target("2.6.0", "beta")
    for version in ("2.5.0", "2.7.0", "3.0.0"):
        with pytest.raises(ValueError, match="frozen"):
            check_beta_bridge_target(version, "beta")
    check_beta_bridge_target("2.6.1", "dev")
    check_beta_bridge_target("3.0.0", "stable")


@MIGRATION_ONLY
def test_migration_notice_uses_existing_once_only_update_flow():
    payload = load_whats_new("2.6.0")
    assert payload["title"] == "Cloth NeXt has moved to Superhive"
    assert [(a["label"], a["kind"]) for a in payload["actions"]] == [
        ("Set up Superhive", "url"), ("Continue", "close")]
    assert payload["actions"][0]["url"].startswith("https://support.superhivemarket.com/")
    state = SeenState(True, ("2.5.0",), "2.5.0")
    assert state.next_screen("2.6.0") == "whats-new"
    assert state.mark_seen("whats-new", "2.6.0").next_screen("2.6.0") is None


@MIGRATION_ONLY
def test_migration_source_contains_no_development_ui():
    root = Path(__file__).resolve().parents[1] / "cloth_next"
    tokens = ("quick_assign", "quickadd", "quick_add", "quickassign")
    for path in root.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        assert not any(token in path.name.lower() for token in tokens), path
        assert not path.name.startswith("quick_"), path
        if path.suffix in (".py", ".json", ".toml", ".md"):
            assert not any(token in path.read_text(encoding="utf-8").lower() for token in tokens), path
