"""Frame-rate-independent motion for the Bake companion icon field."""
from __future__ import annotations

import math


def subpixel_coordinate(value: float, phases: int = 4) -> tuple[int, int]:
    """Split a position into an integer Canvas coordinate and image phase.

    Tk places bitmap images on device pixels even when Canvas coordinates are
    floats. A pre-rendered fractional image phase carries the remaining offset
    so slow particles do not pause for many frames and then jump by one pixel.
    """
    phases = max(1, int(phases))
    quantized = math.floor(float(value) * phases + 0.5)
    coordinate, phase = divmod(quantized, phases)
    return coordinate, phase


def smooth_rate(current: float, target: float, elapsed: float,
                response: float = 5.0) -> float:
    """Approach *target* with the same response at every display frame rate."""
    elapsed = max(0.0, min(float(elapsed), 0.1))
    return target + (current - target) * math.exp(-response * elapsed)


def advance_particle(particle: dict, elapsed: float, rate: float,
                     width: float, height: float) -> tuple[float, float]:
    """Advance one linear path and sample bounded perpendicular path noise."""
    elapsed = max(0.0, min(float(elapsed), 0.1))
    motion_time = elapsed * max(0.0, float(rate))
    particle["base_x"] += particle["direction_x"] * particle["speed"] * motion_time
    particle["base_y"] += particle["direction_y"] * particle["speed"] * motion_time
    particle["noise_time"] += motion_time

    margin = 16.0
    span_x, span_y = width + margin * 2.0, height + margin * 2.0
    particle["base_x"] = (particle["base_x"] + margin) % span_x - margin
    particle["base_y"] = (particle["base_y"] + margin) % span_y - margin

    noise_time = particle["noise_time"]
    noise = particle["amplitude"] * (
        math.sin(particle["phase"] + noise_time * particle["frequency"])
        + 0.35 * math.sin(particle["phase_2"]
                          + noise_time * particle["frequency_2"]))
    # Perpendicular displacement bends the path without changing its forward
    # speed or accumulating a new positional error every timer callback.
    perpendicular_x = -particle["direction_y"]
    perpendicular_y = particle["direction_x"]
    return (particle["base_x"] + perpendicular_x * noise,
            particle["base_y"] + perpendicular_y * noise)


def advance_veyra_particle(particle: dict, elapsed: float, rate: float,
                           width: float, height: float) -> tuple[float, float]:
    """Advance a bounded repair/convergence loop toward the center seam."""
    duration = max(0.8, float(particle.get("duration", 2.4)))
    particle["veyra_time"] = (float(particle.get("veyra_time", 0.0))
                              + max(0.0, elapsed) * max(0.05, rate)) % duration
    phase = particle["veyra_time"] / duration
    eased = phase * phase * (3.0 - 2.0 * phase)
    start_x = min(width, max(0.0, float(particle.get("start_x", 0.0))))
    start_y = min(height, max(0.0, float(particle.get("start_y", height / 2))))
    seam_x = width / 2 + float(particle.get("seam_offset", 0.0))
    x = start_x + (seam_x - start_x) * eased
    y = start_y + math.sin(phase * math.pi) * float(
        particle.get("vertical_arc", 0.0))
    return min(width, max(0.0, x)), min(height, max(0.0, y))
