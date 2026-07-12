"""Run the complete non-publishing release rehearsal on Windows."""
from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True, shell=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("complete preflight requires Windows to build the real companion EXE")
    version = tomllib.loads((ROOT/"cloth_next/blender_manifest.toml").read_text("utf-8"))["version"]
    archive = ROOT/"dist"/f"cloth_next-{version}-windows-x64.zip"
    py = sys.executable
    (ROOT/"cloth_next/bin/cloth-next-bake.exe").unlink(missing_ok=True)
    (ROOT/"cloth_next/companion_manifest.json").unlink(missing_ok=True)
    run(py, "-m", "pytest", "-m", "not integration and not built_artifact")
    run(py, "tools/validate_extension.py", "cloth_next", "--phase", "source")
    run(py, "tools/build_icons.py")
    run(py, "companion/build_companion.py")
    companion = ROOT/"companion/dist/Cloth NeXt Bake.exe"
    run(py, "tools/scan_companion.py", str(companion))
    run(py, "tools/stage_companion.py", str(companion))
    run(py, "tools/build_extension.py", "--blender", args.blender, "--output", str(archive))
    run(py, "-m", "pytest", "-m", "built_artifact", "--extension-zip", str(archive))
    run(args.blender, "--factory-startup", "--background", "--command", "extension",
        "validate", str(archive))
    run(py, "tools/scan_release_artifact.py", str(archive))
    run(py, "tools/validate_release_policy.py", "--phase", "post-build",
        "--tag", f"v{version}", "--zip", str(archive))
    print(f"unpublished release preflight passed: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
