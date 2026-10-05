# SPDX-License-Identifier: GPL-3.0-or-later
"""Hard face ownership and audited triangle material tables (no Blender imports).

Keys audited in PPF 0.11's scalar expansion and Gaia 0.23's
frontend/_scene_.py and crates/ppf-cts-solver/src/scene.rs. Values here are
solver units; friction is converted from artist units by the Blender adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import struct
from types import MappingProxyType
from uuid import UUID

PARAMETERS = MappingProxyType({
    "young-mod": ("Stretch Resistance", 0.0, 1e9),
    "bend": ("Bend Resistance", 0.0, 3.4028234663852886e38),
    "friction": ("Friction", 0.0, 0.5),
    "deformation-damping": ("Shape Damping", 0.0, 3.4028234663852886e38),
    "bending-damping": ("Fold Damping", 0.0, 3.4028234663852886e38),
})


class ZoneError(ValueError):
    """An artist-readable error, raised before a solver process starts."""


@dataclass(frozen=True)
class MaterialZone:
    identity: str
    token: int
    name: str
    enabled: bool
    overrides: tuple[tuple[str, float], ...]


def topology_signature(vertex_count, polygons):
    """Ordered connectivity, including winding and polygon boundaries."""
    digest = hashlib.sha256(struct.pack('<Q', vertex_count))
    for face in polygons:
        digest.update(struct.pack('<Q', len(face)))
        for index in face:
            digest.update(struct.pack('<Q', int(index)))
    return digest.hexdigest()


def validate_tables(name, tables, count):
    for key, values in tables.items():
        if key not in PARAMETERS:
            raise ZoneError(f"{name}: unsupported Material Zone property {key!r}. Remove it.")
        if len(values) != count:
            raise ZoneError(f"{name}: {key} needs {count} triangle values. Revalidate the mesh.")
        label, low, high = PARAMETERS[key]
        if any(not math.isfinite(v) or not low <= v <= high
               or (key == 'young-mod' and v <= 0) for v in values):
            accepted = f'positive and at most {high:g}' if key == 'young-mod' else f'between {low:g} and {high:g}'
            raise ZoneError(f"{name}: {label} must be finite, {accepted}. Correct the override.")


def triangle_tables(name, owners, zones, triangle_polygons, base, friction=()):
    """Expand face ownership directly; shared vertices never enter this calculation."""
    by_token, identities, zone_overrides = {}, set(), {}
    for zone in zones:
        try:
            valid = str(UUID(zone.identity)) == zone.identity
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid or zone.identity in identities or zone.token <= 0 or zone.token in by_token:
            raise ZoneError(f"{name}: Material Zone {zone.name!r} has an invalid/duplicate identity. Remove and recreate it.")
        identities.add(zone.identity)
        by_token[zone.token] = zone
        overrides = dict(zone.overrides)
        zone_overrides[zone.token] = overrides
        if len(overrides) != len(zone.overrides):
            raise ZoneError(f"{name}: Material Zone {zone.name!r} repeats a property. Remove the duplicate.")
        validate_tables(f"{name}, Material Zone {zone.name!r}",
                        {key: (value,) for key, value in overrides.items()}, 1)
    if any(type(owner) is not int or (owner != 0 and owner not in by_token) for owner in owners):
        raise ZoneError(f"{name}: Material Zone face ownership is invalid. Clear selections and reselect faces.")
    if any(not 0 <= index < len(owners) for index in triangle_polygons):
        raise ZoneError(f"{name}: Material Zone triangle mapping is invalid. Apply topology modifiers and reselect faces.")
    keys = sorted({key for zone in zones if zone.enabled for key, _ in zone.overrides})
    tables = {}
    for key in keys:
        values = []
        for ordinal, polygon in enumerate(triangle_polygons):
            zone = by_token.get(owners[polygon])
            inherited = friction[ordinal] if key == 'friction' and len(friction) else base[key]
            value = zone_overrides[zone.token].get(key, inherited) if zone and zone.enabled else inherited
            values.append(float(struct.unpack('<f', struct.pack('<f', value))[0]))
        tables[key] = tuple(values)
    validate_tables(name, tables, len(triangle_polygons))
    return tables


def assign_faces(owners, indices, token, remove=False):
    result = None
    for index in indices:
        current = owners[index] if result is None else result[index]
        target = 0 if remove and current == token else current if remove else token
        if target != current:
            if result is None:
                result = list(owners)
            result[index] = target
    return tuple(result) if result is not None else owners


class ScreenBins:
    """Session projection cache; query only cells overlapping the circle."""
    def __init__(self, points, cell=64):
        self.cell, self.bins = cell, {}
        for index, point in enumerate(points):
            if point is not None:
                bucket = (math.floor(point[0]/cell), math.floor(point[1]/cell))
                self.bins.setdefault(bucket, []).append((index, point))

    def circle(self, cursor, radius):
        x, y = cursor
        for bx in range(math.floor((x-radius)/self.cell), math.floor((x+radius)/self.cell)+1):
            for by in range(math.floor((y-radius)/self.cell), math.floor((y+radius)/self.cell)+1):
                for index, point in self.bins.get((bx, by), ()):
                    if (point[0]-x)**2 + (point[1]-y)**2 <= radius**2:
                        yield index


def visible_center(center, origin, direction, raycast, face, epsilon):
    """First surface identity AND position, independent of face normal or view type."""
    hit = raycast(origin, direction)
    if hit is None or hit[0] != face:
        return False
    return sum((a-b)**2 for a, b in zip(hit[1], center)) <= epsilon**2
