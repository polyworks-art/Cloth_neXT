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

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (5, 1, 2)

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
        if match and tuple(map(int, match.groups())) == EXPECTED:
            return path.resolve(), first
    raise FileNotFoundError("Blender 5.1.2 not found. Searched:\n" + "\n".join(searched))

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender")
    args = parser.parse_args(argv)
    blender, version = resolve(args.blender)
    print(f"Executable: {blender}")
    print(f"Detected: {version}")
    command = [str(blender), "--background", "--factory-startup", "--python",
               str(ROOT / "tools/blender_smoke_test.py")]
    with tempfile.TemporaryDirectory(prefix="clothnext-blender-") as temp:
        env = os.environ.copy()
        env.update(BLENDER_USER_CONFIG=str(Path(temp) / "config"),
                   BLENDER_USER_SCRIPTS=str(Path(temp) / "scripts"),
                   BLENDER_USER_DATAFILES=str(Path(temp) / "datafiles"))
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                text=True)
        output = (result.stdout or "") + (result.stderr or "")
        print(output)
        marker = "Cloth NeXt registration smoke test passed"
        return result.returncode if result.returncode else (0 if marker in output else 1)

if __name__ == "__main__":
    raise SystemExit(main())
