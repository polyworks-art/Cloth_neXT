# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded obstacle-aware air velocity; an authored approximation, not CFD."""
from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class ObstacleSamples:
    distance: np.ndarray
    normal: np.ndarray
    center: np.ndarray
    scale: np.ndarray
    inside: np.ndarray
    blocked_distance: np.ndarray
    blocked_scale: np.ndarray
    blocked_center: np.ndarray


def obstacle_velocity(points, flow, source, seconds, sampler, *, wake_strength=.35, wake_length=3.):
    """Trace upstream through geometry, divert near surfaces, author a wake.

    `sampler` supplies evaluated mesh queries without making this numerical
    layer depend on Blender. Each source is treated independently, so opposing
    winds cast their own shadows rather than sharing one dominant direction.
    """
    points = np.asarray(points, float)
    flow = np.asarray(flow, float)
    if (points.shape != flow.shape or points.shape[-1] != 3
            or not np.isfinite((points, flow)).all()
            or not np.isfinite((seconds, wake_strength, wake_length)).all()
            or not 0 <= wake_strength <= 2 or wake_length <= 0):
        raise ValueError('Invalid obstacle Wind inputs')
    result = flow.copy().reshape(-1, 3)
    positions = points.reshape(-1, 3)
    speeds = np.linalg.norm(result, axis=1)
    active = speeds > 1e-6
    if not active.any():
        return flow.copy()
    p, v, speed = positions[active], result[active], speeds[active]
    direction = np.asarray(source.direction, float).copy()
    direction /= np.linalg.norm(direction)
    relative = p - source.center
    along = relative @ direction
    lateral = relative - along[:, None] * direction
    # Only obstacles within this local emitter's support can shadow its flow.
    reach = along + np.sqrt(np.maximum(0., source.radius**2 - np.sum(lateral*lateral, axis=1)))
    samples = sampler.sample(p, direction, np.maximum(0., reach))
    scale = np.maximum(samples.scale, 1e-4)
    proximity = np.exp(-samples.distance / (scale * .4))
    entering = np.minimum(0., np.sum(v * samples.normal, axis=1))
    # Remove inward flow near the surface; route the intercepted portion
    # towards the silhouette's sides, leaving a center stagnation point.
    v = v - samples.normal * (entering * proximity)[:, None]
    offset = p - samples.center
    escape = offset - (offset @ direction)[:, None] * direction
    escape /= np.maximum(np.linalg.norm(escape, axis=1)[:, None], 1e-6)
    v += escape * (-entering * proximity * .5)[:, None]
    blocked = np.isfinite(samples.blocked_distance)
    shadow = np.zeros(len(p))
    shadow[blocked] = np.exp(-samples.blocked_distance[blocked] /
                             (np.maximum(samples.blocked_scale[blocked], 1e-4) * wake_length))
    v *= (1. - .92 * shadow)[:, None]
    if wake_strength:
        reference = np.array((0., 0., 1.)) if abs(direction[2]) < .9 else np.array((0., 1., 0.))
        side = np.cross(direction, reference); side /= np.linalg.norm(side)
        up = np.cross(direction, side)
        delta = p - samples.blocked_center
        width = np.maximum(samples.blocked_scale, 1e-4)
        phase = seconds * 2 * math.pi / source.noise_time
        phase = phase - np.where(blocked, samples.blocked_distance, 0.) / width * 3
        phase += (delta @ side) / width + source.seed % 997 / 997 * 2 * math.pi
        swirl = np.sin(phase)[:, None] * side + np.cos(phase)[:, None] * up
        v += swirl * (speed * shadow * wake_strength)[:, None]
    # This authoring model must not amplify velocity without bound where
    # obstacle layers overlap. Interior samples always have zero air velocity.
    magnitude = np.linalg.norm(v, axis=1)
    v *= np.minimum(1., speed * 1.5 / np.maximum(magnitude, 1e-12))[:, None]
    v[samples.inside] = 0.
    result[active] = v
    return result.reshape(flow.shape)
