# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve Blender 5.1.2 and run the repository smoke script."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "cloth_next" / "blender_manifest.toml"


def minimum_blender_version() -> tuple[int, int, int]:
    value = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))[
        "blender_version_min"]
    parts = tuple(int(part) for part in str(value).split("."))
    if len(parts) != 3:
        raise ValueError(f"invalid blender_version_min {value!r}")
    return parts


def supported_blender_version(value: tuple[int, int, int]) -> bool:
    return value >= minimum_blender_version()

def candidates(explicit: str | None = None):
    values = [explicit, os.environ.get("CLOTH_NEXT_BLENDER"),
              os.environ.get("BLENDER_EXECUTABLE"), shutil.which("blender"),
              shutil.which("blender.exe")]
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    values += [program_files / "Blender Foundation/Blender 5.1/blender.exe",
               program_files / "Blender Foundation/Blender 5.1.2/blender.exe",
               local / "Programs/Blender Foundation/Blender 5.1/blender.exe",
               local / "Programs/Blender Foundation/Blender 5.1.2/blender.exe",
               Path(r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"),
               Path(r"C:\Program Files\Steam\steamapps\common\Blender\blender.exe")]
    seen = set()
    for value in values:
        if value and str(value) not in seen:
            seen.add(str(value)); yield Path(value)

def resolve(explicit: str | None = None) -> tuple[Path, str]:
    searched = []
    for path in candidates(explicit):
        searched.append(str(path))
        if not path.is_file(): continue
        result = subprocess.run([str(path), "--version"], capture_output=True,
                                text=True, timeout=30)
        first = (result.stdout or result.stderr).splitlines()[0]
        match = re.search(r"Blender (\d+)\.(\d+)\.(\d+)", first)
        if match and supported_blender_version(tuple(map(int, match.groups()))):
            return path.resolve(), first
    minimum = ".".join(map(str, minimum_blender_version()))
    raise FileNotFoundError(
        f"Blender {minimum} or newer not found. Searched:\n"
        + "\n".join(searched))

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender")
    args = parser.parse_args(argv)
    blender, version = resolve(args.blender)
    print(f"Executable: {blender}")
    print(f"Detected: {version}")
    command = [str(blender), "--background", "--factory-startup",
               "--python-exit-code", "1", "--python",
               str(ROOT / "tools/blender_smoke_test.py")]
    with tempfile.TemporaryDirectory(prefix="clothnext-blender-") as temp:
        env = os.environ.copy()
        # Blender 5.x extension repositories and their Python wheels live
        # below the unified user-resources root.  The older per-category
        # variables do not isolate that state and can mutate the artist's
        # normal extension cache during a supposedly clean smoke test.
        env["BLENDER_USER_RESOURCES"] = str(Path(temp) / "resources")
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                text=True)
        output = (result.stdout or "") + (result.stderr or "")
        print(output)
        marker = "Cloth NeXt registration smoke test passed"
        return result.returncode if result.returncode else (0 if marker in output else 1)

if __name__ == "__main__":
    raise SystemExit(main())
