"""Reproducible TrustMark P/Q ThreadMark quality and attack benchmark."""

from __future__ import annotations
import argparse
import io
import json
import math
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageEnhance, ImageFilter, ImageDraw  # noqa: E402
import numpy as np  # noqa: E402
from cloth_next.provenance.detection import detect_threadmark  # noqa: E402
from cloth_next.provenance.payload import ThreadMarkPayloadV1  # noqa: E402
from verifier.trustmark_backend import TrustMarkOnnxBackend  # noqa: E402


def corpus():
    result = []
    a = np.zeros((512, 640, 3), dtype=np.uint8)
    x = np.linspace(0, 255, 640, dtype=np.uint8)
    a[:, :, 0] = x
    a[:, :, 1] = x[::-1]
    a[:, :, 2] = 96
    im = Image.fromarray(a)
    d = ImageDraw.Draw(im)
    d.ellipse((140, 80, 500, 440), fill=(210, 80, 35))
    result.append(("gradient_shapes", im))
    rng = np.random.default_rng(1729)
    noise = rng.normal(127, 42, (512, 640, 3)).clip(0, 255).astype(np.uint8)
    result.append(
        ("photo_like_noise", Image.fromarray(noise).filter(ImageFilter.GaussianBlur(2)))
    )
    y, xg = np.indices((512, 640))
    check = (((xg // 24 + y // 24) % 2) * 155 + 50).astype(np.uint8)
    result.append(
        (
            "checker",
            Image.fromarray(
                np.dstack((check, np.roll(check, 9, 1), np.roll(check, 17, 0)))
            ),
        )
    )
    return result


def jpeg(im, q):
    stream = io.BytesIO()
    im.convert("RGB").save(stream, "JPEG", quality=q)
    stream.seek(0)
    with Image.open(stream) as reopened:
        return reopened.copy()


def attacks(im):
    w, h = im.size
    yield "original", im
    for q in (95, 85, 70):
        yield f"jpeg_{q}", jpeg(im, q)
    for f in (0.75, 0.5, 0.25):
        yield (
            f"resize_{int(f * 100)}",
            im.resize((int(w * f), int(h * f)), Image.Resampling.LANCZOS),
        )
    for f in (0.10, 0.20, 0.40):
        dx, dy = int(w * f / 2), int(h * f / 2)
        yield f"crop_{int(f * 100)}", im.crop((dx, dy, w - dx, h - dy))
    yield "brightness", ImageEnhance.Brightness(im).enhance(1.08)
    yield "contrast", ImageEnhance.Contrast(im).enhance(1.08)
    arr = np.asarray(im, dtype=np.float32) / 255
    yield "gamma", Image.fromarray(np.rint(np.power(arr, 0.92) * 255).astype(np.uint8))
    yield (
        "sharpen",
        im.filter(ImageFilter.UnsharpMask(radius=1, percent=60, threshold=3)),
    )
    yield "blur", im.filter(ImageFilter.GaussianBlur(0.7))
    n = np.random.default_rng(42).normal(0, 2, np.asarray(im).shape)
    yield "noise", Image.fromarray(np.clip(np.asarray(im) + n, 0, 255).astype(np.uint8))
    yield "resize_jpeg", jpeg(im.resize((w // 2, h // 2), Image.Resampling.LANCZOS), 85)
    yield "crop_jpeg", jpeg(im.crop((w // 10, h // 10, w - w // 10, h - h // 10)), 85)
    yield (
        "resize_sharpen_jpeg",
        jpeg(
            im.resize((w // 2, h // 2), Image.Resampling.LANCZOS).filter(
                ImageFilter.SHARPEN
            ),
            85,
        ),
    )
    canvas = Image.new("RGB", (w + 320, h + 220), (38, 41, 47))
    canvas.paste(im, (160, 110))
    yield "screenshot", canvas


def quality(before, after):
    a, b = (
        np.asarray(before.convert("RGB"), dtype=np.float32),
        np.asarray(after.convert("RGB"), dtype=np.float32),
    )
    diff = np.abs(a - b)
    mse = float(np.mean((a - b) ** 2))
    psnr = 100.0 if mse == 0 else 20 * math.log10(255 / math.sqrt(mse))
    try:
        from skimage.metrics import structural_similarity

        ssim = float(structural_similarity(a, b, channel_axis=2, data_range=255))
    except ImportError:
        ssim = None
    return {
        "psnr_db": psnr,
        "ssim": ssim,
        "max_abs_difference": float(diff.max()),
        "mean_abs_difference": float(diff.mean()),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    bits = ThreadMarkPayloadV1().to_bits()
    report = {"schema": 1, "configurations": [], "false_positives": []}
    for variant in ("P", "Q"):
        for strength in (0.8, 0.9, 1.0):
            cold = time.perf_counter()
            backend = TrustMarkOnnxBackend(
                args.models, variant=variant, strength=strength
            )
            cold = time.perf_counter() - cold
            rows = []
            for name, cover in corpus():
                start = time.perf_counter()
                marked = backend.encode(cover, bits)
                encode_s = time.perf_counter() - start
                for attack, changed in attacks(marked):
                    start = time.perf_counter()
                    det = detect_threadmark(changed, backend)
                    elapsed = time.perf_counter() - start
                    rows.append(
                        {
                            "image": name,
                            "attack": attack,
                            "status": det.status.value,
                            "confidence": det.confidence,
                            "regions_matched": det.regions_matched,
                            "regions_tested": det.regions_tested,
                            "decode_seconds": elapsed,
                        }
                    )
                rows[-len(tuple(attacks(marked)))]["quality"] = quality(cover, marked)
                rows[-len(tuple(attacks(marked)))]["encode_seconds"] = encode_s
            negatives = []
            for name, image in corpus():
                for attack, changed in attacks(image):
                    det = detect_threadmark(changed, backend)
                    negatives.append(
                        {"image": name, "attack": attack, "status": det.status.value}
                    )
            report["configurations"].append(
                {
                    "variant": variant,
                    "strength": strength,
                    "ecc": "BCH_SUPER",
                    "cold_load_seconds": cold,
                    "results": rows,
                    "negatives": negatives,
                }
            )
            print(
                f"{variant} {strength:.2f}: {sum(r['status'] == 'DETECTED' for r in rows)}/{len(rows)} detected; false positives {sum(r['status'] == 'DETECTED' for r in negatives)}"
            )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
