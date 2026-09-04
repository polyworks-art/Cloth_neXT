# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the development-only Tk companion executable."""

from __future__ import annotations
import argparse
from pathlib import Path
import os
import sys
import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from companion.build_assets import (  # noqa: E402
    PARTICLE_ASSETS,
    PARTICLE_SUBPIXEL_ASSETS,
    STATUS_ASSETS,
    build as build_assets,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threadmark-models", type=Path)
    args = parser.parse_args()
    model_root = args.threadmark_models
    if model_root is None and os.environ.get("THREADMARK_MODEL_DIR"):
        model_root = Path(os.environ["THREADMARK_MODEL_DIR"])
    if model_root is None:
        raise FileNotFoundError("pre-provisioned ThreadMark models are required")
    models = [model_root / name for name in ("encoder_Q.onnx", "decoder_Q.onnx")]
    if not all(path.is_file() for path in models):
        raise FileNotFoundError("Q encoder/decoder models must be pre-provisioned")
    build_assets()
    assets = ROOT / "companion/assets"
    PyInstaller.__main__.run(
        [
            str(ROOT / "companion/app.py"),
            "--name=Cloth NeXt Bake",
            "--onefile",
            "--windowed",
            "--noconfirm",
            # Pillow's AVIF codec is larger than four compressed MiB and ThreadMark
            # intentionally supports only PNG/JPEG/WebP/TIFF. Keep the release
            # archive below GitHub Pages' hard 100 MiB git-blob limit.
            "--exclude-module=PIL.AvifImagePlugin",
            "--exclude-module=PIL._avif",
            f"--icon={assets / 'cloth_next.ico'}",
            f"--add-data={assets / 'cloth_next.png'};companion_assets",
            f"--add-data={assets / 'veyra.png'};companion_assets",
            f"--add-data={assets / 'bake.png'};companion_assets",
            *[f"--add-data={path};threadmark_models" for path in models],
            *[
                f"--add-data={assets / name};companion_assets"
                for name in PARTICLE_ASSETS
            ],
            *[
                f"--add-data={assets / name};companion_assets"
                for name in PARTICLE_SUBPIXEL_ASSETS
            ],
            *[f"--add-data={assets / name};companion_assets" for name in STATUS_ASSETS],
            f"--distpath={ROOT / 'companion/dist'}",
            f"--workpath={ROOT / 'companion/build/app_icon'}",
            f"--specpath={ROOT / 'companion'}",
            f"--paths={ROOT}",
        ]
    )
    output = ROOT / "companion/dist/Cloth NeXt Bake.exe"
    if not output.is_file():
        raise RuntimeError("companion EXE was not produced")
    print(f"Development companion: {output}")


if __name__ == "__main__":
    main()
