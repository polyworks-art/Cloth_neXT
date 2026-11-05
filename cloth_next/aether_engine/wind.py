# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloth NeXt Wind (Force Empties) for the AETHER Engine (API 1.4 ``set_inputs.wind``).

The engine evaluates the same air-velocity field as the Wind preview and the GAIA export
(``gaia.wind_field.velocity``: smooth spherical sources plus 8 bounded curl modes per source) and the
GAIA air model: a vertex's velocity is pulled toward the air velocity along its normal (and by Air
Friction tangentially) with ``air density / time step`` x vertex area. This module turns the authored
sources into that input; the curl modes use the very same seeded numbers as ``velocity``.
"""
from __future__ import annotations

import hashlib
import math

import numpy as np

MAX_SOURCES = 8


def modes(seed: int) -> list[list[float]]:
    """The 8 curl modes of ``gaia.wind_field.velocity`` for ``seed``: [wave xyz, axis xyz, phase]."""
    out = []
    for index in range(8):
        digest = hashlib.sha256(f"{seed}:{index}".encode()).digest()
        numbers = np.frombuffer(digest[:24], dtype="<u4").astype(float) / (2 ** 32 - 1) * 2 - 1
        wave = numbers[:3] / np.linalg.norm(numbers[:3])
        axis = np.cross(wave, numbers[3:6])
        axis /= max(float(np.linalg.norm(axis)), 1e-12)
        out.append([*map(float, wave), *map(float, axis), digest[24] / 255 * 2 * math.pi])
    return out


def source_input(source, seconds_at_engine_frame0: float) -> dict:
    """One ``gaia.wind_field.WindSource`` as an engine source.

    The engine's clock starts at its frame 0; the field's clock is the scene time, so the start time is
    folded into the mode phases."""
    direction = np.asarray(source.direction, float)
    flow = direction / np.linalg.norm(direction) * float(source.speed)
    shift = seconds_at_engine_frame0 * 2 * math.pi / float(source.noise_time)
    return {"center": [float(v) for v in source.center], "flow": [float(v) for v in flow],
            "radius": float(source.radius), "falloff": float(source.falloff),
            "turbulence": float(source.turbulence), "noise_size": float(source.noise_size),
            "noise_time": float(source.noise_time),
            "modes": [m[:6] + [m[6] + shift] for m in modes(int(source.seed))]}


def wind_input(sources, air_density: float, air_friction: float, time_step: float,
               seconds_at_engine_frame0: float) -> dict:
    """The engine's ``wind`` input; no sources or no air density switch the wind off."""
    sources = list(sources)[:MAX_SOURCES]
    drag = float(air_density) / float(time_step) if time_step > 0 and air_density > 0 else 0.0
    return {"drag": drag, "friction": float(air_friction),
            "sources": [source_input(s, seconds_at_engine_frame0) for s in sources] if drag > 0 else []}


def velocity(points, wind: dict, seconds_since_engine_frame0: float):
    """Reference evaluation of an engine ``wind`` input (tests: equals ``gaia.wind_field.velocity``)."""
    p = np.asarray(points, float)
    w = np.zeros_like(p)
    for s in wind["sources"]:
        r = p - np.asarray(s["center"])
        q = np.maximum(0.0, 1.0 - np.einsum("...i,...i->...", r, r) / s["radius"] ** 2)
        f = np.broadcast_to(np.asarray(s["flow"]), p.shape).copy()
        if s["turbulence"]:
            for m in s["modes"]:
                phase = r @ np.asarray(m[:3]) * (2 * math.pi / s["noise_size"])
                phase += seconds_since_engine_frame0 * 2 * math.pi / s["noise_time"] + m[6]
                f += s["turbulence"] / 8 * np.cos(phase)[..., None] * np.asarray(m[3:6])
        w += f * (q ** s["falloff"])[..., None]
    return w
