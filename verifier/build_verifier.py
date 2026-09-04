"""Build the offline development verifier with explicitly supplied models."""

from __future__ import annotations
import argparse
from pathlib import Path
import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, required=True)
    args = parser.parse_args()
    required = [args.models / name for name in ("encoder_Q.onnx", "decoder_Q.onnx")]
    if not all(path.is_file() for path in required):
        raise FileNotFoundError("Q encoder/decoder models must be pre-provisioned")
    assets = ROOT / "companion/assets"
    dist = ROOT / "verifier/dist"
    PyInstaller.__main__.run(
        [
            str(ROOT / "verifier/app.py"),
            "--name=Cloth NeXt Verify",
            "--onefile",
            "--windowed",
            "--noconfirm",
            f"--icon={assets / 'cloth_next.ico'}",
        f"--add-data={required[0]};threadmark_models",
        f"--add-data={required[1]};threadmark_models",
            f"--distpath={dist}",
            f"--workpath={ROOT / 'verifier/build'}",
            f"--specpath={ROOT / 'verifier'}",
        f"--paths={ROOT}",
        ]
    )
    output = dist / "Cloth NeXt Verify.exe"
    if not output.is_file():
        raise RuntimeError("verifier EXE was not produced")
    print(f"Verifier: {output} ({output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
