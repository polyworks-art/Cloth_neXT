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
* Rayleigh damping [s] = shape / fold damping (different formulation: approximate);
* Shrink / Shrink Warp / Shrink Weft = the cloth's rest-shape factors along the
  UV map's V (warp, the grain) and U (weft) axis (engine API 1.5);
* Surface Offset [m] = a collision skin the engine adds in full to every contact
  distance of the cloth (engine API 1.5), as PPF's contact-offset.

* Permanent Stretch / Permanent Bends = the engine's plasticity (API 1.6),
  the same dead-zone creep GAIA uses: rate per second, stretch threshold as a
  fraction, bend threshold in radians;
* Use Initial Bend Shape off = flat bending rest of the cloth (API 1.6);
* Inflate Pressure [Pa] = the engine's pressure (API 1.6), same force as GAIA's.

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
    # Engine API 1.5, sent beside the material (per triangle / per cloth), not inside it:
    shrink_weft: float = 1.0    # rest-size factor across the grain (UV U), 0.9 = 10 % smaller
    shrink_warp: float = 1.0    # rest-size factor along the grain (UV V)
    surface_offset: float = 0.0 # m, collision skin of the cloth

    @property
    def shrinks(self) -> bool:
        return self.shrink_weft != 1.0 or self.shrink_warp != 1.0

    # engine API 1.6: Permanent Stretch / Permanent Bends (rates 1/s, 0 = off; thresholds: fraction, radians)
    plastic_rate: float = 0.0
    plastic_threshold: float = 0.0
    bend_plastic_rate: float = 0.0
    bend_plastic_threshold: float = 0.0
    # engine API 1.6, per cloth (sent beside the material, see cloth_protocol): flat bending rest, pressure [Pa]
    flat_bending_rest: bool = False
    pressure: float = 0.0

    def cloth_protocol(self) -> dict:
        """The cloth-level fields of a create_session cloth (not part of a zone material)."""
        out = {}
        if self.flat_bending_rest:
            out["flat_bending_rest"] = True
        if self.pressure > 0.0:
            out["pressure"] = self.pressure
        return out

    def as_protocol(self) -> dict:
        out = {"density": self.density, "youngs": self.youngs, "poisson": self.poisson,
               "shear_scale": self.shear_scale, "bend": self.bend,
               "stretch_damping": self.stretch_damping, "bend_damping": self.bend_damping,
               "friction": self.friction, "thickness": self.thickness}
        if self.strain_limit > 0.0:
            out["strain_limit"] = self.strain_limit
        if self.plastic_rate > 0.0:
            out["plastic_rate"] = self.plastic_rate
            out["plastic_threshold"] = self.plastic_threshold
        if self.bend_plastic_rate > 0.0:
            out["bend_plastic_rate"] = self.bend_plastic_rate
            out["bend_plastic_threshold"] = self.bend_plastic_threshold
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
    "surface_offset": ("EXACT", "Collision skin added to every contact distance of the cloth (engine API 1.5); "
                                "a Collider's Surface Offset adds to it."),
    "shrink_percent": ("EXACT", "Rest-shape scale (engine API 1.5); combined with Shrink Warp / Weft."),
    "shrink_warp_percent": ("EXACT", "Rest-shape scale along the grain = the UV map's V axis (engine API 1.5)."),
    "shrink_weft_percent": ("EXACT", "Rest-shape scale across the grain = the UV map's U axis (engine API 1.5)."),
    "stretch_plasticity": ("APPROXIMATE", "Engine plasticity (API 1.6), GAIA's dead-zone creep of the rest shape; "
                                          "applied once per frame instead of every substep."),
    "bend_plasticity": ("APPROXIMATE", "Engine bend plasticity (API 1.6), GAIA's dead-zone creep of the hinge "
                                       "rest angles; applied once per frame instead of every substep."),
    "bend_rest_from_geometry": ("EXACT", "Off: flat bending rest of the cloth (API 1.6). Edges where three or more "
                                         "panels meet keep their initial angle, as in GAIA's builder."),
    "inflate_pressure": ("APPROXIMATE", "Pressure [Pa] along each triangle's normal (API 1.6), GAIA's force; "
                                        "evaluated at the start of each substep instead of implicitly."),
}


def unsupported_controls(material) -> list[str]:
    """Active controls AETHER cannot simulate (empty: fully mappable)."""
    found = []
    model = str(getattr(material, "model", "FABRIC"))
    if model != "FABRIC":
        found.append("Shape-Preserving material model (AETHER simulates Fabric only)")
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
    from ..materials.formatting import shrink_factors
    weft, warp = shrink_factors(material)
    for name, factor in (("Shrink Weft", weft), ("Shrink Warp", warp)):
        if not (math.isfinite(factor) and 0.0 < factor <= 10.0):
            raise UnsupportedMaterial(f"{name}: the combined rest size {factor:g} is outside (0, 10]")
    offset = _finite("Surface Offset", getattr(material, "surface_offset", 0.0))
    if not 0.0 <= offset <= 1.0:
        raise UnsupportedMaterial("Surface Offset must be between 0 and 1 m for AETHER")
    return AetherMaterial(
        density=density, youngs=youngs, poisson=poisson, shear_scale=1.0, bend=bend,
        stretch_damping=max(MIN_DAMPING, _finite("Shape Damping", material.shape_damping)),
        bend_damping=max(MIN_FOLD_DAMPING if live else 0.0, _finite("Fold Damping", material.fold_damping)),
        friction=max(0.0, _finite("Surface Grip", material.surface_grip)) * FRICTION_SCALE,
        thickness=max(MIN_HALF_GAP, 0.5 * gap), strain_limit=strain_limit(material, live),
        shrink_weft=float(weft), shrink_warp=float(warp), surface_offset=float(offset),
        **plasticity(material), flat_bending_rest=not bool(getattr(material, "bend_rest_from_geometry", True)),
        pressure=pressure(material))


def plasticity(material) -> dict:
    """Permanent Stretch / Permanent Bends as the engine's plasticity fields (API 1.6)."""
    out = {}
    if getattr(material, "stretch_plasticity_enabled", False):
        rate = _finite("Stretch Creep Rate", getattr(material, "stretch_plasticity_rate", 0.0))
        threshold = _finite("Stretch Threshold", getattr(material, "stretch_plasticity_threshold_percent", 0.0))
        if rate < 0.0 or threshold < 0.0:
            raise UnsupportedMaterial("Permanent Stretch rate and threshold must not be negative")
        if rate > 0.0:
            out["plastic_rate"] = rate
            out["plastic_threshold"] = threshold / 100.0
    if getattr(material, "bend_plasticity_enabled", False):
        rate = _finite("Bend Creep Rate", getattr(material, "bend_plasticity_rate", 0.0))
        threshold = _finite("Bend Threshold", getattr(material, "bend_plasticity_threshold_degrees", 0.0))
        if rate < 0.0 or not 0.0 <= threshold <= 180.0:
            raise UnsupportedMaterial("Permanent Bends rate must not be negative and its threshold must be 0-180°")
        if rate > 0.0:
            out["bend_plastic_rate"] = rate
            out["bend_plastic_threshold"] = math.radians(threshold)
    return out


def pressure(material) -> float:
    """Inflate Pressure [Pa] (0 when off), the same quantity GAIA receives."""
    if not getattr(material, "enable_inflate", False):
        return 0.0
    value = _finite("Pressure", getattr(material, "inflate_pressure", 0.0))
    if value < 0.0:
        raise UnsupportedMaterial("Pressure must not be negative")
    return value


def seam_stiffness(strength: float, a: AetherMaterial, b: AetherMaterial) -> float:
    """Sewing Strength 0..100 -> AETHER seam stiffness (N/m per mean edge).

    100 is AETHER's default seam (10 x the panels' membrane modulus 2 mu + lambda,
    Milestone 4); lower strengths scale it linearly. 0 is refused upstream."""
    def modulus(m: AetherMaterial) -> float:
        mu = m.youngs / (2.0 * (1.0 + m.poisson))
        lam = m.youngs * m.poisson / (1.0 - m.poisson * m.poisson)
        return 2.0 * mu + lam
    return float(strength) / 100.0 * 10.0 * 0.5 * (modulus(a) + modulus(b))
