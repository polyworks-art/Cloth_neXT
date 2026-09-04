# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit build-time fetch of the pinned Adobe TrustMark Q ONNX models."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import urllib.request

BASE_URL = "https://cai-watermark.adobe.net/watermarking/trustmark-models"
MODELS = {
    "encoder_Q.onnx": "19b3d1b25836130ffd78775a8f61539f993375d1823ef0e59ba5b8dffb4f892d",
    "decoder_Q.onnx": "ee3268f057c9dabef680e169302f5973d0589feea86189ed229a896cc3aa88df",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for name, expected in MODELS.items():
        target = output / name
        if target.is_file() and _sha256(target) == expected:
            results.append(target)
            continue
        temporary = target.with_suffix(target.suffix + ".partial")
        temporary.unlink(missing_ok=True)
        try:
            urllib.request.urlretrieve(f"{BASE_URL}/{name}", temporary)
            actual = _sha256(temporary)
            if actual != expected:
                raise ValueError(f"{name} SHA-256 mismatch")
            temporary.replace(target)
            results.append(target)
        finally:
            temporary.unlink(missing_ok=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for path in fetch(args.output):
        print(f"ThreadMark build model verified: {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
