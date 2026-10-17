# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the development-only Tk companion executable."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import argparse

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.build_assets import (  # noqa: E402
    PARTICLE_ASSETS,
    PARTICLE_SUBPIXEL_ASSETS,
    STATUS_ASSETS,
    build as build_assets,
)
from cloth_next.platform_support import platform_spec  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-root', type=Path,
                        help='Isolated development build root; preserves existing Companion artifacts')
    args = parser.parse_args()
    build_root = args.output_root.resolve() if args.output_root else ROOT/'companion'
    build_root.mkdir(parents=True, exist_ok=True)
    build_assets()
    assets = ROOT / "companion/assets"
    PyInstaller.__main__.run(
        [
            str(ROOT / "companion/app.py"),
            "--name=Cloth NeXt Bake",
            "--onefile",
            "--windowed",
            "--noconfirm",
            '--hidden-import=cryptography.hazmat.primitives.asymmetric.ed25519',
            *([f"--icon={assets / 'cloth_next.ico'}"] if sys.platform == "win32" else []),
            f"--add-data={assets / 'cloth_next.png'}{os.pathsep}companion_assets",
            f"--add-data={assets / 'veyra.png'}{os.pathsep}companion_assets",
            f"--add-data={assets / 'bake.png'}{os.pathsep}companion_assets",
            *[
                f"--add-data={assets / name}{os.pathsep}companion_assets"
                for name in PARTICLE_ASSETS
            ],
            *[
                f"--add-data={assets / name}{os.pathsep}companion_assets"
                for name in PARTICLE_SUBPIXEL_ASSETS
            ],
            *[
                f"--add-data={assets / name}{os.pathsep}companion_assets"
                for name in STATUS_ASSETS
            ],
            f"--distpath={build_root / 'dist'}",
            f"--workpath={build_root / 'build/app_icon'}",
            f"--specpath={build_root}",
            f"--paths={ROOT}",
        ]
    )
    output = build_root / 'dist' / platform_spec().companion_build_name
    if not output.is_file():
        raise RuntimeError("companion executable was not produced")
    print(f"Development companion: {output}")


if __name__ == "__main__":
    main()
