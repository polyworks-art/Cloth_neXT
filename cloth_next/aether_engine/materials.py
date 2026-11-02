# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloth NeXt material settings -> AETHER Engine physical material (pure).

Cloth NeXt's material values are PPF wire conventions (docs/PPF_PARAMETER_
MAPPING.md): ``stretch_resistance`` is the density-normalized PPF young-mod
and ``bend_resistance`` the PPF bend calibrated from measured fabric bending
(docs/MATERIAL_LIBRARY.md). AETHER takes physical SI values, so the mapping
derives them instead of copying numbers:

* membrane modulus [N/m]  = stretch_resistance x surface_weight
  (undoes PPF's density normalization);
* bending stiffness [N m] = bend_resistance x surface_weight / 14.3491
  x 2.86967e-3 (inverts the documented MIT calibration
  ``bend = B[lbf in^2] x 14.3491 / surface_weight`` and converts lbf in^2 to SI),
  then x 0.6 for AETHER's measured discrete-bending stiffening;
* Poisson ratio = sideways_response (same quantity);
* friction coefficient = surface_grip x 0.5 (the coefficient PPF receives);
* collision half-gap [m] = collision_gap / 2 (AETHER's cloth-cloth gap is the
  sum of both half-gaps);
* Rayleigh damping [s] = shape / fold damping (different formulation: approximate).

Everything else either has no AETHER equivalent and is refused when it is
active (never silently approximated), or is reported as approximate.
``docs/AETHER_ENGINE_INTEGRATION.md`` lists the measured reference cases.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os

MAPPING_VERSION = 2
LBF_IN2_TO_NM2 = 4.4482216152605 * 0.0254 ** 2   # 1 lbf in^2 in N m^2
PPF_BEND_CALIBRATION = 14.3491                    # docs/MATERIAL_LIBRARY.md
FRICTION_SCALE = 0.5                              # surface_grip -> coefficient (as for PPF)
# Rayleigh damping [s] every AETHER cloth gets at least (Live Simulation and bakes): fabric loses energy
# when it bends and stretches, and without it AETHER's cloth kept swinging and wobbling where Marvelous
# falls calm and heavy. 0.005 s matched Marvelous best on the black-box sphere drape (RMS to MD 102 vs
# 116 mm at frame 30; 0.02 s already fell too slowly). Bakes get it since engine 7feac19 (damping turns
# with the cloth): before, it held back cloth riding on the turning NEON character (380 ms/frame, body
# intersections); now 115 ms/frame and 0 intersections, as fast as undamped. Fold (bending) damping
# stays Live-only: with it, the animated NEON vest snagged (Animation quality: 5.5x stretched edges,
# 20 cloth-cloth intersections); stretch damping alone is clean in all three qualities.
# Higher Shape / Fold Damping values are used as set.
MIN_DAMPING = float(os.environ.get("CLOTH_NEXT_AETHER_MIN_DAMPING", "0.005"))
MIN_FOLD_DAMPING = float(os.environ.get("CLOTH_NEXT_AETHER_MIN_FOLD_DAMPING", "0.005"))   # Live only
MIN_HALF_GAP = 1.0e-4                             # m; AETHER needs a positive contact gap
# Measured (tools/aether_material_calibration.py, clamped cantilever, small
# deflection): AETHER's discrete dihedral bending is 1.5-1.9x stiffer than the
# continuum stiffness it is given (ratio 0.53-0.65 over five mesh resolutions
# and aspect ratios). The mapping hands it 0.6 x B so the drape matches B.
DISCRETE_BENDING_CALIBRATION = 0.6

# GAIA look (production readiness, measured 2026-11-02): with the same Cloth NeXt material, GAIA folds
# a 1 m sheet over a sphere like AETHER with far less bending stiffness, and how much less depends on
# the mesh resolution (GAIA's bending is not resolution-consistent, AETHER's is). Matched on the GAIA
# test drape by the mean fold angle and the shape distance at three resolutions (median edge -> factor):
# 41.7 mm -> 0.01, 20.8 mm -> 0.055, 10.4 mm -> 0.17. Interpolated in log-log, extrapolated with the
# last slope towards finer meshes, never above 1. CLOTH_NEXT_AETHER_GAIA_BENDING=0 keeps the
# continuum calibration alone.
GAIA_BENDING_POINTS = ((0.0104, 0.17), (0.0208, 0.055), (0.0417, 0.01))


def gaia_bending_factor(median_edge: float) -> float:
    """Bending multiplier that makes AETHER fold like GAIA at this mesh resolution (1 = off)."""
    import os
    if os.environ.get("CLOTH_NEXT_AETHER_GAIA_BENDING") == "0" or not (median_edge > 0.0):
        return 1.0
    pts = [(math.log(h), math.log(f)) for h, f in GAIA_BENDING_POINTS]
    x = math.log(median_edge)
    if x <= pts[0][0]:
        (x0, y0), (x1, y1) = pts[0], pts[1]
    elif x >= pts[-1][0]:
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
    else:
        i = next(k for k in range(len(pts) - 1) if pts[k][0] <= x <= pts[k + 1][0])
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
    y = y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return max(0.005, min(1.0, math.exp(y)))


class UnsupportedMaterial(ValueError):
    """A material control is active that AETHER cannot represent."""


@dataclass(frozen=True, slots=True)
class AetherMaterial:
    density: float
    youngs: float
    poisson: float
    shear_scale: float
    bend: float
    stretch_damping: float
    bend_damping: float
    friction: float
    thickness: float
    strain_limit: float = 0.0   # largest principal stretch above rest (0.05 = 5 %), 0 = none (engine API 1.3)

    def as_protocol(self) -> dict:
        out = {"density": self.density, "youngs": self.youngs, "poisson": self.poisson,
               "shear_scale": self.shear_scale, "bend": self.bend,
               "stretch_damping": self.stretch_damping, "bend_damping": self.bend_damping,
               "friction": self.friction, "thickness": self.thickness}
        if self.strain_limit > 0.0:
            out["strain_limit"] = self.strain_limit
        return out


# canonical field -> (kind, explanation); kinds as in simulation.backends.MappingKind
FIELD_MAPPINGS = {
    "surface_weight": ("EXACT", "Area density in kg/m²."),
    "stretch_resistance": ("APPROXIMATE", "Membrane modulus = Stretch Resistance × Surface Weight (N/m); "
                                          "AETHER uses an StVK membrane, PPF Baraff-Witkin."),
    "sideways_response": ("EXACT", "Poisson ratio."),
    "bend_resistance": ("APPROXIMATE", "Bending stiffness from the measured-bending calibration "
                                       "(N·m); dihedral discretization differs from PPF."),
    "stretch_limit": ("APPROXIMATE", "Enforced in the Live Simulation (engine strain limit, API 1.3: a penalty "
                                     "plus a projection per substep). Bakes run without it and report it: with the "
                                     "limit, animated characters got slower and intersected (NEON). "
                                     "CLOTH_NEXT_AETHER_STRAIN_LIMIT=1 / 0 forces it on / off everywhere."),
    "shape_damping": ("APPROXIMATE", "Rayleigh membrane damping in seconds."),
    "fold_damping": ("APPROXIMATE", "Rayleigh bending damping in seconds."),
    "surface_grip": ("APPROXIMATE", "Coulomb coefficient = grip × 0.5; colliders use the cloth's value."),
    "collision_gap": ("APPROXIMATE", "Cloth half-gap = gap / 2; colliders use the cloth's gap."),
    "surface_offset": ("UNSUPPORTED", "No contact offset in AETHER: refused when non-zero."),
}


def unsupported_controls(material) -> list[str]:
    """Active controls AETHER cannot simulate (empty: fully mappable)."""
    found = []
    model = str(getattr(material, "model", "FABRIC"))
    if model != "FABRIC":
        found.append("Shape-Preserving material model (AETHER simulates Fabric only)")
    if getattr(material, "stretch_plasticity_enabled", False):
        found.append("Permanent Stretch")
    if getattr(material, "bend_plasticity_enabled", False):
        found.append("Permanent Bends")
    if not getattr(material, "bend_rest_from_geometry", True):
        found.append("flat bend rest shape (AETHER always uses the initial shape)")
    if getattr(material, "enable_inflate", False) and float(getattr(material, "inflate_pressure", 0.0)) != 0.0:
        found.append("Inflate / Pressure")
    if float(getattr(material, "shrink_percent", 0.0)) != 0.0:
        found.append("Shrink")
    if float(getattr(material, "surface_offset", 0.0)) != 0.0:
        found.append("Surface Offset")
    if getattr(material, "sewing_enabled", False):
        found.append("loose-edge Sewing (use Sewing definitions)")
    return found


def strain_limit_enabled(live: bool = False) -> bool:
    """Stretch Limit is enforced in the Live Simulation, not in bakes.

    With the limit, animated NEON bakes ran 2.5x slower and intersected the body (Normal), so bakes run
    without it for now; the Live Simulation, where the artist pulls the cloth, keeps it.
    CLOTH_NEXT_AETHER_STRAIN_LIMIT=1 / 0 forces it on / off for both."""
    import os
    forced = os.environ.get("CLOTH_NEXT_AETHER_STRAIN_LIMIT")
    if forced in {"0", "1"}:
        return forced == "1"
    return live


def not_enforced(material, live: bool = False) -> list[str]:
    """Active controls the AETHER Engine bakes without enforcing them.

    Reported to the artist on the panel, in the bake result and in the cache
    sidecar; never presented as exact. Stretch Limit is enabled in almost every
    bundled preset, so refusing it would refuse nearly every garment."""
    found = []
    if getattr(material, "stretch_limit_enabled", False) and not strain_limit_enabled(live):
        found.append(f"Stretch Limit {float(getattr(material, 'maximum_stretch_percent', 0.0)):g} % "
                     "(not enforced in AETHER bakes, only in the Live Simulation; stretch follows the "
                     "membrane stiffness)")
    return found


def strain_limit(material, live: bool = False) -> float:
    """Stretch Limit as the engine's strain limit (0 = off, in bakes or when switched off)."""
    if not getattr(material, "stretch_limit_enabled", False) or not strain_limit_enabled(live):
        return 0.0
    percent = _finite("Maximum Stretch", getattr(material, "maximum_stretch_percent", 0.0))
    if percent <= 0.0:
        raise UnsupportedMaterial("Maximum Stretch must be greater than zero")
    return percent / 100.0


def physical_bending(material) -> float:
    """Continuum bending stiffness per unit width (N m) the preset stands for."""
    return (_finite("Bend Resistance", material.bend_resistance) * float(material.surface_weight)
            / PPF_BEND_CALIBRATION * LBF_IN2_TO_NM2)


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise UnsupportedMaterial(f"{name} must be finite")
    return value


def map_material(material, live: bool = False) -> AetherMaterial:
    """Physical AETHER material for one ShellMaterialSettings. Raises UnsupportedMaterial."""
    missing = unsupported_controls(material)
    if missing:
        raise UnsupportedMaterial("Not supported by the AETHER Engine: " + ", ".join(missing))
    density = _finite("Surface Weight", material.surface_weight)
    if density <= 0.0:
        raise UnsupportedMaterial("Surface Weight must be greater than zero")
    youngs = _finite("Stretch Resistance", material.stretch_resistance) * density
    bend = physical_bending(material) * DISCRETE_BENDING_CALIBRATION
    poisson = _finite("Sideways Response", material.sideways_response)
    if not 0.0 <= poisson < 0.5:
        raise UnsupportedMaterial("Sideways Response must be at least 0 and below 0.5 for AETHER")
    gap = _finite("Collision Gap", material.collision_gap)
    return AetherMaterial(
        density=density, youngs=youngs, poisson=poisson, shear_scale=1.0, bend=bend,
        stretch_damping=max(MIN_DAMPING, _finite("Shape Damping", material.shape_damping)),
        bend_damping=max(MIN_FOLD_DAMPING if live else 0.0, _finite("Fold Damping", material.fold_damping)),
        friction=max(0.0, _finite("Surface Grip", material.surface_grip)) * FRICTION_SCALE,
        thickness=max(MIN_HALF_GAP, 0.5 * gap), strain_limit=strain_limit(material, live))


def seam_stiffness(strength: float, a: AetherMaterial, b: AetherMaterial) -> float:
    """Sewing Strength 0..100 -> AETHER seam stiffness (N/m per mean edge).

    100 is AETHER's default seam (10 x the panels' membrane modulus 2 mu + lambda,
    Milestone 4); lower strengths scale it linearly. 0 is refused upstream."""
    def modulus(m: AetherMaterial) -> float:
        mu = m.youngs / (2.0 * (1.0 + m.poisson))
        lam = m.youngs * m.poisson / (1.0 - m.poisson * m.poisson)
        return 2.0 * mu + lam
    return float(strength) / 100.0 * 10.0 * 0.5 * (modulus(a) + modulus(b))
