# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Editable TETHYS wetness profiles and their Material Zone resolution (no bpy).

Profiles are configurable starting points, not certified measurements.
They are independent of the PPF mechanical material: nothing here is
inferred from friction, bend or stretch values.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from types import MappingProxyType


class WetnessError(ValueError):
    """An artist-readable TETHYS configuration error."""


@dataclass(frozen=True)
class Parameter:
    key: str
    label: str
    unit: str
    to_si: float       # artist value * to_si = engine (SI) value
    minimum: float
    maximum: float
    description: str


# Order is the engine's material row layout (protocol v1).
PARAMETERS = (
    Parameter("absorption_rate", "Absorption Rate", "1/s", 1.0, 0.0, 1000.0,
              "How quickly surface water soaks into the fabric"),
    Parameter("water_capacity", "Water Capacity", "mm", 1e-3, 0.0, 50.0,
              "Water the fabric can hold when saturated, as a film thickness"),
    Parameter("surface_mobility", "Surface Mobility", "", 1.0, 0.0, 10.0,
              "How freely surface water flows, relative to a free water film"),
    Parameter("water_repellency", "Water Repellency", "", 1.0, 0.0, 1.0,
              "Resistance to wetting: slows first absorption and holds water back at zone borders"),
    Parameter("capillary_diffusion", "Capillary Diffusion", "mm²/s", 1e-6, 0.0, 10000.0,
              "How fast absorbed moisture wicks through the fabric"),
    Parameter("evaporation_rate", "Evaporation Rate", "×", 1.0, 0.0, 100.0,
              "Drying speed relative to the scene's ambient drying rate"),
    Parameter("boundary_permeability", "Boundary Permeability", "", 1.0, 0.0, 1.0,
              "Share of water that may cross into a different material (0 blocks, 1 is open)"),
    Parameter("surface_retention", "Surface Retention", "mm", 1e-3, 0.0, 10.0,
              "Film thickness held in place before water starts to run off or drip"),
    Parameter("flow_anisotropy", "Flow Anisotropy", "", 1.0, 0.05, 20.0,
              "Flow along the fabric's U direction relative to across it (1 is even)"),
    Parameter("surface_variation", "Surface Variation", "", 1.0, 0.0, 1.0,
              "Irregularity of the weave or coating: gathers runoff into streaks (0 is perfectly even)"),
)
PARAMETER_BY_KEY = MappingProxyType({p.key: p for p in PARAMETERS})


@dataclass(frozen=True)
class WetnessProfile:
    identifier: str
    label: str
    description: str
    absorption_rate: float
    water_capacity: float
    surface_mobility: float
    water_repellency: float
    capillary_diffusion: float
    evaporation_rate: float
    boundary_permeability: float
    surface_retention: float
    flow_anisotropy: float
    surface_variation: float

    def values(self) -> dict[str, float]:
        return {p.key: float(getattr(self, p.key)) for p in PARAMETERS}

    def with_overrides(self, overrides) -> "WetnessProfile":
        changes = {}
        for key, value in overrides:
            if key not in PARAMETER_BY_KEY:
                raise WetnessError(f"Unsupported TETHYS property {key!r}. Remove it.")
            changes[key] = float(value)
        return replace(self, **changes)


def _profile(identifier, label, description, *values):
    return WetnessProfile(identifier, label, description, *map(float, values))


#                       absorb  cap mm  mobil  repel  wick  evap  perm  retain  aniso  variation
PROFILES = MappingProxyType({p.identifier: p for p in (
    _profile("COTTON", "Cotton", "Strong absorption and wicking, holds moisture long",
             4.0, 0.35, 0.05, 0.05, 8.0, 1.0, 1.0, 0.15, 1.0, 0.3),
    _profile("WOOL", "Wool", "Resists first wetting, then absorbs; slow to dry",
             0.4, 0.30, 0.03, 0.5, 3.0, 0.7, 0.8, 0.30, 1.0, 0.45),
    _profile("SILK", "Silk", "Moderate wetting, spreads along the weave, sheds water",
             1.5, 0.10, 0.15, 0.2, 5.0, 1.2, 0.9, 0.05, 3.0, 0.35),
    _profile("LEATHER", "Leather (treated)", "Low absorption, moderate runoff, slow exchange",
             0.05, 0.20, 0.12, 0.6, 0.3, 0.4, 0.3, 0.08, 1.0, 0.55),
    _profile("WATERPROOF", "Waterproof Nylon / Coated", "Minimal absorption, strong runoff",
             0.01, 0.002, 1.0, 0.95, 0.02, 1.5, 0.1, 0.03, 1.0, 0.7),
    _profile("RUBBER", "Rubber / Latex", "No internal moisture; water only on the surface",
             0.0, 0.0, 1.5, 1.0, 0.0, 1.5, 0.0, 0.02, 1.0, 0.5),
    _profile("ABSORBENT_LINING", "Absorbent Lining", "Terry-like lining that soaks and wicks quickly",
             8.0, 0.80, 0.02, 0.0, 12.0, 0.9, 1.0, 0.20, 1.0, 0.2),
    _profile("DENIM", "Denim", "Heavy cotton twill: absorbent, wicks along the twill",
             2.5, 0.45, 0.04, 0.1, 6.0, 0.8, 0.9, 0.20, 1.6, 0.4),
    _profile("SYNTHETIC", "Synthetic Weave", "Uncoated polyester or nylon: little uptake, quick drying",
             0.8, 0.05, 0.25, 0.4, 4.0, 1.3, 0.8, 0.06, 1.2, 0.5),
    _profile("GENERIC", "Generic Fabric", "Neutral woven fabric",
             2.0, 0.25, 0.08, 0.2, 4.0, 1.0, 1.0, 0.10, 1.0, 0.3),
)})
DEFAULT_PROFILE = "GENERIC"
AUTO = "AUTO"
INHERIT = "INHERIT"

# Fabric presets whose fibre or coating is known from the preset itself.
# Anything else falls back to GENERIC; mechanical values are never used.
_PRESET_PROFILES = {
    "SILK": "SILK", "MIT_SILK_LIGHT": "SILK", "MIT_SILK_SATIN": "SILK", "MIT_SILK_MEDIUM": "SILK",
    "COTTON": "COTTON", "MIT_COTTON_LIGHT": "COTTON", "MIT_COTTON_MEDIUM": "COTTON",
    "MIT_LINEN": "COTTON", "MIT_CORDUROY": "COTTON", "MIT_CANVAS": "COTTON",
    "PRODUCT_TENCEL_LYOCELL_JERSEY": "COTTON",
    "WOOL": "WOOL", "MIT_WOOL_HEAVY": "WOOL", "MIT_WOOL_UPHOLSTERY": "WOOL",
    "DENIM": "DENIM", "MIT_DENIM": "DENIM",
    "LEATHER": "LEATHER",
    "MIT_TERRY_KNIT": "ABSORBENT_LINING",
    "MIT_PLEATHER": "WATERPROOF", "MIT_FLANNEL_VINYL": "WATERPROOF", "MIT_LAMINATED_COTTON": "WATERPROOF",
    "PRODUCT_GORETEX_ARCTIC_3L": "WATERPROOF", "PRODUCT_GORETEX_PRO_3L": "WATERPROOF",
    "PRODUCT_XPAC_VX21": "WATERPROOF", "PRODUCT_XPAC_VX21_SOFT": "WATERPROOF",
    "PRODUCT_XPAC_LS21": "WATERPROOF", "PRODUCT_DYNEEMA_DCF_055": "WATERPROOF",
    "PRODUCT_DYNEEMA_DCF_16": "WATERPROOF", "PRODUCT_CHALLENGE_ULTRA_100": "WATERPROOF",
    "PRODUCT_CHALLENGE_ULTRA_200": "WATERPROOF", "PRODUCT_TYVEK_400": "WATERPROOF",
    "PRODUCT_PERTEX_SHIELD_AIR": "WATERPROOF",
    "MIT_NYLON_RIPSTOP": "SYNTHETIC", "MIT_OUTDOOR_POLYESTER": "SYNTHETIC", "MIT_TAFFETA": "SYNTHETIC",
    "MIT_LYCRA_LIGHT": "SYNTHETIC", "MIT_LYCRA_HEAVY": "SYNTHETIC", "MIT_LYCRA_JERSEY": "SYNTHETIC",
    "PRODUCT_LYCRA_SPORT": "SYNTHETIC", "PRODUCT_COOLMAX_ECOMADE_JERSEY": "SYNTHETIC",
    "PRODUCT_ADIDAS_TIRO25_MATCH": "SYNTHETIC", "PRODUCT_ADIDAS_TIRO25_TRAINING": "SYNTHETIC",
    "PRODUCT_CORDURA_CLASSIC_500D": "SYNTHETIC", "PRODUCT_CORDURA_CLASSIC_1000D": "SYNTHETIC",
    "PRODUCT_CORDURA_BALLISTIC_1680D": "SYNTHETIC", "PRODUCT_PERTEX_QUANTUM": "SYNTHETIC",
    "PRODUCT_PERTEX_QUANTUM_AIR": "SYNTHETIC", "PRODUCT_POLARTEC_POWER_GRID": "SYNTHETIC",
}


def profile_for_preset(preset_identifier: str | None) -> str:
    """Wetness profile for a fabric preset, or GENERIC when no reliable mapping exists."""
    return _PRESET_PROFILES.get(str(preset_identifier or ""), DEFAULT_PROFILE)


def profile(identifier: str) -> WetnessProfile:
    try:
        return PROFILES[identifier]
    except KeyError:
        raise WetnessError(f"Unknown wetness profile {identifier!r}. Choose a profile.") from None


def validate(values: dict[str, float], where: str) -> None:
    for parameter in PARAMETERS:
        value = values[parameter.key]
        if not math.isfinite(value) or not parameter.minimum <= value <= parameter.maximum:
            raise WetnessError(
                f"{where}: {parameter.label} must be between {parameter.minimum:g} and "
                f"{parameter.maximum:g} {parameter.unit}".rstrip() + ". Correct the value.")


@dataclass(frozen=True)
class ZoneWetness:
    token: int
    name: str
    enabled: bool
    profile: str                         # INHERIT or a profile identifier
    overrides: tuple[tuple[str, float], ...]


def resolve_materials(name, base_profile: str, base_overrides, zones, owners, triangle_polygons):
    """Material rows (engine SI units) and one row index per triangle.

    Row 0 is the Base Material. An enabled zone with its own profile or
    overrides gets its own row; a disabled zone, or one that changes
    nothing, inherits row 0. Ownership comes straight from the FACE-domain
    owner of each triangle's source polygon; shared vertices play no part.
    """
    base = profile(base_profile).with_overrides(base_overrides)
    rows = [base.values()]
    validate(rows[0], f"{name}, Base Material")
    row_of_token = {}
    seen_tokens = set()
    for zone in zones:
        if zone.token <= 0 or zone.token in seen_tokens:
            raise WetnessError(f"{name}: Material Zone {zone.name!r} has an invalid identity. Recreate it.")
        seen_tokens.add(zone.token)
        if not zone.enabled or (zone.profile == INHERIT and not zone.overrides):
            row_of_token[zone.token] = 0
            continue
        source = base if zone.profile == INHERIT else profile(zone.profile)
        values = source.with_overrides(zone.overrides).values()
        validate(values, f"{name}, Material Zone {zone.name!r}")
        row_of_token[zone.token] = len(rows)
        rows.append(values)
    material_of = []
    for polygon in triangle_polygons:
        if not 0 <= polygon < len(owners):
            raise WetnessError(f"{name}: triangle mapping is invalid. Apply topology modifiers.")
        owner = owners[polygon]
        if owner == 0:
            material_of.append(0)
        elif owner in row_of_token:
            material_of.append(row_of_token[owner])
        else:
            raise WetnessError(f"{name}: Material Zone face ownership is invalid. Clear selections and reselect faces.")
    si_rows = [[values[p.key] * p.to_si for p in PARAMETERS] for values in rows]
    return si_rows, material_of
