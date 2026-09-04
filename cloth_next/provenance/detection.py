# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bounded regional ThreadMark detection and conservative aggregation."""

from __future__ import annotations
from .payload import ThreadMarkPayloadV1
from .protocol import DetectionStatus, ThreadMarkDetectionResult

MIN_REGION_EDGE = 96


def candidate_regions(image):
    width, height = image.size
    boxes = [("full", (0, 0, width, height))]
    for fraction in (0.90, 0.75, 0.60):
        w, h = int(width * fraction), int(height * fraction)
        boxes.append(
            (
                f"center_{int(fraction * 100)}",
                (
                    (width - w) // 2,
                    (height - h) // 2,
                    (width + w) // 2,
                    (height + h) // 2,
                ),
            )
        )
    # Four overlapping 70% windows: bounded coverage for screenshots/UI borders.
    w, h = int(width * 0.70), int(height * 0.70)
    boxes.extend(
        (name, box)
        for name, box in (
            ("top_left", (0, 0, w, h)),
            ("top_right", (width - w, 0, width, h)),
            ("bottom_left", (0, height - h, w, height)),
            ("bottom_right", (width - w, height - h, width, height)),
        )
    )
    seen = set()
    for name, box in boxes:
        if box in seen or min(box[2] - box[0], box[3] - box[1]) < MIN_REGION_EDGE:
            continue
        seen.add(box)
        yield name, image.crop(box)


def detect_threadmark(image, decoder) -> ThreadMarkDetectionResult:
    if min(image.size) < MIN_REGION_EDGE:
        return ThreadMarkDetectionResult(
            DetectionStatus.INCONCLUSIVE,
            0.0,
            False,
            None,
            0,
            0,
            diagnostics={"reason": "image resolution too low"},
        )
    tested = matched = 0
    confidences = []
    errors = []
    payload_bits = ""
    for name, region in candidate_regions(image):
        tested += 1
        try:
            signal = decoder.decode(region)
            if not signal.ecc_valid:
                continue
            ThreadMarkPayloadV1.from_bits(signal.payload_bits)
            matched += 1
            confidences.append(signal.confidence)
            payload_bits = signal.payload_bits
        except ValueError:
            continue
        except Exception as exc:  # bounded diagnostics; verifier never shows traceback
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    if matched >= 2:
        status = DetectionStatus.DETECTED
    elif matched == 1:
        status = DetectionStatus.LIKELY
    elif tested == 0 or len(errors) == tested:
        status = DetectionStatus.INCONCLUSIVE
    else:
        status = DetectionStatus.NOT_DETECTED
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return ThreadMarkDetectionResult(
        status,
        round(confidence, 6),
        bool(matched),
        1 if matched else None,
        tested,
        matched,
        payload_bits,
        {
            "region_limit": 8,
            "decoder_errors": errors[:8],
            "reason": ("insufficient matching regions" if matched == 1 else ""),
        },
    )
