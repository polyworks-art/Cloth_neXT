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
    import tomllib
    repos = bpy.context.preferences.extensions.repos
    repo = next((r for r in repos if r.module == "migration_test"), None)
    if repo is None:
        for other in repos:
            other.enabled = False
        repo = repos.new(name="Native release regression", module="migration_test",
                         remote_url=config["url"], source="USER")
    identity = (repo.module, repo.directory, repo.remote_url)
    repo.enabled = True
    manifest = Path(repo.directory) / "cloth_next/blender_manifest.toml"
    if phase == "update":
        assert tomllib.loads(manifest.read_text(encoding="utf-8"))["version"] == config["older_version"]
    assert bpy.ops.extensions.repo_sync(repo_directory=repo.directory) == {"FINISHED"}
    index = json.loads((Path(repo.directory) / ".blender_ext/index.json").read_text(encoding="utf-8"))
    if phase == "update":
        old = importlib.import_module("bl_ext.migration_test.cloth_next.updater.addon_updates")
        session = old.AddonUpdateSession()
        old.run_update_check(session, old.UpdateChannel.BETA,
            old.parse_version(config["older_version"]), fetch=lambda _: index)
        assert session.state in old.ACTIONABLE_STATES
    enabled = [r for r in repos if r.enabled and r.remote_url]
    repo_index = next(i for i, r in enumerate(enabled) if r.directory == repo.directory)
    assert bpy.ops.extensions.package_install(repo_index=repo_index, pkg_id="cloth_next") == {"FINISHED"}
    module = importlib.import_module("bl_ext.migration_test.cloth_next")
    expected = config["older_version"] if phase == "older" else config["expected"]
    assert module.manifest_version() == expected
    module.register()
    manager = importlib.import_module(module.__name__ + ".blender.onboarding_manager")
    preferences = manager._preferences()
    marker = Path(repo.directory) / "preserved-user-cache.txt"
    if phase == "older":
        preferences.onboarding_state = manager.SeenState(True, (expected,), expected).to_json()
        marker.write_text("preserve", encoding="utf-8")
        bpy.ops.wm.save_userpref()
    else:
        updates = importlib.import_module(module.__name__ + ".blender.addon_update_operators")
        model = updates.addon_updates
        selected = updates.selected_channel(bpy.context)
        assert isinstance(selected, model.RepositoryChannel)
        updates.prepare_repository(bpy.context, selected)
        assert (repo.module, repo.directory, repo.remote_url) == identity
        assert "update_channel" not in preferences.bl_rna.properties
        assert "dev_channel_acknowledged" not in preferences.bl_rna.properties
        if phase == "update":
            assert marker.read_text(encoding="utf-8") == "preserve"
            assert manager._state().next_screen(expected) == "whats-new"
        assert bpy.types.Operator.bl_rna_get_subclass_py("CLOTHNEXT_OT_quick_assign") is not None
        obj = bpy.context.active_object
        assert bpy.ops.clothnext.quick_assign_role(role="RIGID_BODY") == {"FINISHED"}
        assert obj.cloth_next.enabled and obj.cloth_next.role == "RIGID_BODY"
        assert bpy.ops.clothnext.remove_physics() == {"FINISHED"}
        for target in ("2.7.1", "2.8.0", "3.0.0"):
            session = model.AddonUpdateSession()
            model.run_update_check(session, selected, model.parse_version(expected),
                fetch=lambda _: {"data": [{"id": "cloth_next", "version": target}]})
            assert session.state is model.AddonUpdateState.UPDATE_AVAILABLE
        status_model = importlib.import_module(module.__name__ + ".superhive_status")
        assert not status_model.repository_status([repo]).connected
        fixture = repos.new(name="Superhive status fixture", module="superhive_status_test",
                            remote_url="https://superhivemarket.com/native-fixture/index.json", source="USER")
        fixture.enabled = True
        assert status_model.repository_status([fixture]).connected
        assert not status_model.repository_status([fixture]).purchase_validated
        cache = Path(fixture.directory) / ".blender_ext/index.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"data": [{"id": "cloth_next", "version": expected}]}), encoding="utf-8")
        assert status_model.repository_status([fixture]).purchase_validated
        manager.load_whats_new(expected)
        preferences.onboarding_state = manager.SeenState(True, (config["older_version"],), config["older_version"]).to_json()
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
        assert manager._state().next_screen(expected) is None
        assert (repo.module, repo.directory, repo.remote_url) == identity
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
    with zipfile.ZipFile(args.zip) as archive:
        expected = tomllib.loads(archive.read("blender_manifest.toml").decode())["version"]
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
                          "older_version": older_version, "expected": expected, "channel": args.channel, "result": str(root / f"{phase}.json")}
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
