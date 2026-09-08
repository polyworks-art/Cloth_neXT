"""Exercise real Blender feed synchronization and all six native replacements.

Uses tiny disposable fixture extensions, an isolated profile and a loopback HTTP
server. No public feed, real installation, release or solver is modified.
Run: python tools/run_blender_channel_repository_regression.py
"""
from __future__ import annotations

import functools
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = {"stable": "2.0.0", "beta": "2.3.0", "dev": "2.3.5"}


def probe():
    import bpy
    sys.path.insert(0, str(ROOT))
    from cloth_next.updater.addon_updates import (
        configure_owning_repo, decide_update, parse_index_versions)
    from cloth_next.updater.addon_versions import parse_version

    base = sys.argv[sys.argv.index("--probe") + 1]
    repos = bpy.context.preferences.extensions.repos
    for repo in repos:
        repo.enabled = False
    repo = repos.new(name="Channel regression", module="channel_regression",
                     remote_url="", source="USER")
    repo.enabled = True
    identity = (repo.module, repo.directory)
    package_id = f"bl_ext.{repo.module}.cloth_next"
    fixture = Path(sys.argv[sys.argv.index("--fixture-root") + 1])
    assert not repo.use_remote_url
    assert bpy.ops.extensions.package_install_files(
        filepath=str(fixture / "stable" / "cloth_next-2.0.0.zip"),
        repo=repo.module) == {"FINISHED"}

    def select(channel):
        choice = SimpleNamespace(name=channel.upper(), label=channel.capitalize(),
                                 index_url=f"{base}/{channel}/index.json")
        directory = configure_owning_repo(repos, package_id, choice)
        assert repo.use_remote_url
        assert bpy.ops.extensions.repo_sync(repo_directory=directory) == {"FINISHED"}
        payload = json.loads(Path(directory, ".blender_ext", "index.json").read_text())
        return choice, parse_index_versions(payload, choice)

    def install():
        # This external test script owns the operation. The fixture is not the
        # running updater and contains only empty register/unregister functions.
        enabled = [item for item in repos if item.enabled and item.remote_url]
        index = next(i for i, item in enumerate(enabled) if item.module == repo.module)
        assert bpy.ops.extensions.package_install(repo_index=index, pkg_id="cloth_next") == {"FINISHED"}
        import tomllib
        return parse_version(tomllib.loads(Path(
            repo.directory, "cloth_next", "blender_manifest.toml").read_text())["version"])

    for source in VERSIONS:
        select(source)
        installed = install()
        for destination in VERSIONS:
            if source == destination:
                continue
            select(source)
            installed = install()
            choice, targets = select(destination)
            decision = decide_update(installed, targets, choice)
            assert decision.state.name == "SWITCH_CHANNEL", decision
            assert install() == targets[0]
            assert (repo.module, repo.directory) == identity
            print(f"NATIVE_CHANNEL_SWITCH {source} -> {destination}: {targets[0]}")
    print("All six native channel replacements passed")


def main():
    sys.path.insert(0, str(ROOT))
    from tools.run_blender_smoke import resolve
    from tools.build_extension_repository import generate_index
    blender, version = resolve()
    print(version, flush=True)
    with tempfile.TemporaryDirectory(prefix="clothnext-channels-") as temp:
        root = Path(temp)
        for channel, target in VERSIONS.items():
            feed = root / channel
            feed.mkdir()
            with zipfile.ZipFile(feed / f"cloth_next-{target}.zip", "w") as archive:
                archive.writestr("blender_manifest.toml", f'''schema_version = "1.0.0"
id = "cloth_next"
version = "{target}"
name = "Channel Test Fixture"
tagline = "Disposable channel regression fixture"
maintainer = "Cloth NeXt tests"
type = "add-on"
blender_version_min = "5.0.0"
license = ["SPDX:GPL-3.0-or-later"]
''')
                archive.writestr("__init__.py", "def register(): pass\ndef unregister(): pass\n")
            generate_index(str(blender), feed)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = os.environ.copy()
            env["BLENDER_USER_RESOURCES"] = str(root / "profile")
            result = subprocess.run([
                str(blender), "--factory-startup", "--background", "--online-mode",
                "--python-exit-code", "1", "--python", str(Path(__file__).resolve()),
                "--", "--probe", f"http://127.0.0.1:{server.server_port}",
                "--fixture-root", str(root)],
                env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)
            print(result.stdout)
            print(result.stderr)
            return result.returncode or (0 if "All six native channel replacements passed"
                                         in result.stdout else 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        raise SystemExit(main())
