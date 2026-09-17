"""Native Blender install/update regression using isolated profiles and local repositories."""
from __future__ import annotations
import argparse
import functools
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def probe(config, phase):
    import bpy
    import importlib
    import time
    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == "migration_test"), None)
    if repo is None:
        for other in repos:
            other.enabled = False
        repo = repos.new(name="Migration test", module="migration_test",
                         remote_url=config["url"], source="USER")
    identity = (repo.module, repo.directory)
    repo.remote_url = config["url"]
    repo.enabled = True
    before = Path(repo.directory) / "cloth_next/blender_manifest.toml"
    if phase == "update":
        import tomllib
        assert tomllib.loads(before.read_text(encoding="utf-8"))["version"] != "2.6.0"
    assert bpy.ops.extensions.repo_sync(repo_directory=repo.directory) == {"FINISHED"}
    index = json.loads((Path(repo.directory) / ".blender_ext/index.json").read_text(encoding="utf-8"))
    if phase == "update":
        old_updates = importlib.import_module("bl_ext.migration_test.cloth_next.updater.addon_updates")
        session = old_updates.AddonUpdateSession()
        old_updates.run_update_check(session, old_updates.UpdateChannel[config["channel"]],
            old_updates.parse_version(config["older_version"]), fetch=lambda _: index)
        assert session.state is old_updates.AddonUpdateState.UPDATE_AVAILABLE, session
    enabled = [r for r in repos if r.enabled and r.remote_url]
    repo_index = next(i for i, r in enumerate(enabled) if r.directory == repo.directory)
    assert bpy.ops.extensions.package_install(repo_index=repo_index, pkg_id="cloth_next") == {"FINISHED"}
    module = importlib.import_module("bl_ext.migration_test.cloth_next")
    expected = config["older_version"] if phase == "older" else "2.6.0"
    assert module.manifest_version() == expected
    module.register()
    manager = importlib.import_module(module.__name__ + ".blender.onboarding_manager")
    preferences = manager._preferences()
    model = importlib.import_module(module.__name__ + ".updater.addon_updates")
    repo.remote_url = model.UpdateChannel[config["channel"]].index_url
    marker = Path(repo.directory) / "migration-preserved-cache.txt"
    if phase == "older":
        preferences.onboarding_state = manager.SeenState(True, (expected,), expected).to_json()
        marker.write_text("preserve user data", encoding="utf-8")
        bpy.ops.wm.save_userpref()
    else:
        updates = importlib.import_module(module.__name__ + ".blender.addon_update_operators")
        assert str(updates.INSTALLED_VERSION) == "2.6.0"
        assert updates.selected_channel(bpy.context).name == config["channel"]
        if config["channel"] == "DEV":
            assert not preferences.dev_channel_acknowledged
            assert not updates.dev_access_error(bpy.context, updates.UpdateChannel.DEV)
        updates.prepare_repository(bpy.context, updates.selected_channel(bpy.context))
        assert repo.remote_url == model.UpdateChannel[config["channel"]].index_url
        if phase == "update":
            assert marker.read_text(encoding="utf-8") == "preserve user data"
            assert manager._state().next_screen("2.6.0") == "whats-new"
        for name in dir(bpy.ops.clothnext):
            assert "quick" not in name.lower(), name
        for prop in preferences.bl_rna.properties:
            assert "quick" not in prop.identifier.lower(), prop.identifier
        for keyconfig in bpy.context.window_manager.keyconfigs:
            for keymap in keyconfig.keymaps:
                for item in keymap.keymap_items:
                    assert not ("cloth" in item.idname.lower() and "quick" in item.idname.lower())
        payload = manager.load_whats_new("2.6.0")
        assert payload["title"] == "Cloth NeXt has moved to Superhive"
        # Exercise the real Companion acknowledgement and persisted once-only state.
        preferences.onboarding_state = manager.SeenState(True, ("2.5.0",), "2.5.0").to_json()
        os.environ["CLOTH_NEXT_COMPANION_AUTO_CLOSE_MS"] = "500"
        ok, message = manager.launch_screen("whats-new")
        assert ok, message
        process = manager._pending[0][0]
        deadline = time.monotonic() + 25
        while manager._pending and time.monotonic() < deadline:
            manager._poll_startup()
            time.sleep(0.05)
        assert not manager._pending
        process.wait(timeout=10)
        assert process.returncode == 0
        assert manager._state().next_screen("2.6.0") is None
        # Repository identity survives; both official feeds are still selectable.
        for channel in (updates.UpdateChannel.BETA, updates.UpdateChannel.DEV):
            repo.remote_url = channel.index_url
            updates.prepare_repository(bpy.context, channel)
            assert repo.remote_url == channel.index_url
            assert (repo.module, repo.directory) == identity
        model = updates.addon_updates
        session = model.AddonUpdateSession()
        model.run_update_check(session, model.UpdateChannel.DEV,
            model.parse_version("2.6.0"), fetch=lambda _: {"data": [{"id": "cloth_next", "version": "2.6.1"}]})
        assert session.state in model.ACTIONABLE_STATES, (session.state, session.message)
        assert str(session.latest) == "2.6.1"
        repo.remote_url = model.UpdateChannel[config["channel"]].index_url
        obj = bpy.context.active_object
        assert bpy.ops.clothnext.add_physics() == {"FINISHED"}
        assert obj.cloth_next.enabled
        assert bpy.ops.clothnext.remove_physics() == {"FINISHED"}
    module.unregister()
    assert "cloth_next" not in bpy.types.Object.bl_rna.properties
    Path(config["result"]).write_text(json.dumps({"phase": phase, "version": expected, "passed": True}), encoding="utf-8")


def main():
    import sys
    if "--probe" in sys.argv:
        i = sys.argv.index("--probe")
        probe(json.loads(Path(sys.argv[i + 1]).read_text(encoding="utf-8")), sys.argv[i + 2])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", required=True)
    parser.add_argument("--older", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--channel", choices=("BETA", "DEV"), default="BETA")
    args = parser.parse_args()
    import tomllib
    import zipfile
    with zipfile.ZipFile(args.older) as archive:
        older_version = tomllib.loads(archive.read("blender_manifest.toml").decode())["version"]
    results = []
    with tempfile.TemporaryDirectory(prefix="clothnext-migration-") as temporary:
        root = Path(temporary)
        served = root / "repository"
        served.mkdir()
        handler = functools.partial(SimpleHTTPRequestHandler, directory=str(served))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            for phase, archive in (("older", args.older), ("update", args.zip), ("fresh", args.zip)):
                staging = root / phase
                staging.mkdir()
                shutil.copyfile(archive, staging / archive.name)
                subprocess.run([args.blender, "--factory-startup", "--command", "extension", "server-generate", f"--repo-dir={staging}"], check=True)
                shutil.copyfile(archive, served / archive.name)
                shutil.copyfile(staging / "index.json", served / "index.json")
                config = {"url": f"http://127.0.0.1:{server.server_address[1]}/index.json",
                          "older_version": older_version, "channel": args.channel, "result": str(root / f"{phase}.json")}
                config_path = root / "config.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                env = dict(os.environ, BLENDER_USER_RESOURCES=str(root / ("fresh-profile" if phase == "fresh" else "update-profile")))
                command = [args.blender, "--background", "--online-mode", "--python-exit-code", "1"]
                if phase != "update":
                    command.append("--factory-startup")
                command += ["--python", str(Path(__file__).resolve()), "--", "--probe", str(config_path), phase]
                subprocess.run(command, env=env, check=True, timeout=180)
                results.append(json.loads(Path(config["result"]).read_text(encoding="utf-8")))
        finally:
            server.shutdown()
            server.server_close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
