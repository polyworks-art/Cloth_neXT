# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run tools/blender_aether_engine_e2e.py in an isolated real Blender.

    python tools/run_aether_blender_e2e.py --engine "<AETHER>/build/release/AETHER Engine.exe" \
        --output <dir> [--only live sewing ...]

Blender's user resources point to a temporary folder whose ``user_default``
extension repository links to this checkout's ``cloth_next``: the artist's
installed extension, preferences and solver selection are never touched.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from run_blender_smoke import resolve  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blender")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    blender, version = resolve(args.blender)
    print(f"Blender: {version} ({blender})", flush=True)
    with tempfile.TemporaryDirectory(prefix="cnx-aether-e2e-") as temp:
        resources = Path(temp) / "resources"
        repo = resources / "extensions" / "user_default"
        repo.mkdir(parents=True)
        subprocess.run(["cmd", "/c", "mklink", "/J", str(repo / "cloth_next"), str(ROOT / "cloth_next")],
                       check=True, capture_output=True)
        env = os.environ.copy()
        env["BLENDER_USER_RESOURCES"] = str(resources)
        env["CLOTH_NEXT_AETHER_ENGINE"] = str(Path(args.engine).resolve())
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [str(blender), "--background", "--factory-startup", "--python-exit-code", "1",
                   "--python", str(ROOT / "tools" / "blender_aether_engine_e2e.py"), "--",
                   "--module", "bl_ext.user_default.cloth_next", "--output", str(args.output.resolve())]
        if args.only:
            command += ["--only", *args.only]
        result = subprocess.run(command, cwd=ROOT, env=env)
        subprocess.run(["cmd", "/c", "rmdir", str(repo / "cloth_next")], capture_output=True)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
