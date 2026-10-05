# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cloth NeXt-owned preparation for the unmodified official Gaia frontend.

This module does not patch, import private frontend state, or relax upstream
validation. Intra seams use native four-column stitch assets, then the public
FixedScene.set_stitch per-row stiffness setter after the official build.
The binding document is CNX state, never an invented upstream PARAM field.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path

# Standalone worker deliberately loads this file without a package namespace.
MATERIAL_KEYS = frozenset(("young-mod", "bend", "friction",
                          "deformation-damping", "bending-damping"))


def uses_official_bridge(resolved) -> bool:
    from .compatibility import protocol_profile
    from .adapters import ADAPTERS
    protocol = getattr(resolved, "protocol_version", None)
    schema = getattr(resolved, "schema_version", None)
    profile = protocol_profile(protocol, schema) if protocol and schema else None
    adapter = ADAPTERS.get(profile.adapter_id) if profile else None
    return bool(adapter and adapter.official_scene_bridge)


def prepare_for_solver(scene, resolved):
    return prepare_scene(scene, enabled=uses_official_bridge(resolved))


def create_owned_project_link(canonical: Path, owned: Path, project_name: str):
    """Keep official path resolution and CNX's recovery ownership boundary.

    No PPF_CTS_DATA_ROOT test-rig overrides: ordinary filesystem links route
    only this unique project into its authenticated CNX run directory.
    Existing foreign/nonempty directories are never overwritten.
    """
    from .project_links import ensure_project_link
    return ensure_project_link(canonical, owned, owned_root=owned.parent,
                               project_name=project_name)

def prepare_scene(scene, *, enabled: bool):
    """Prepare once, before recovery/cache identities are constructed."""
    if not enabled or scene.official_bridge_json:
        return scene
    # The standalone official-Python worker loads only the binding helpers by
    # filename. Package-specific encoding imports belong to this package API.
    from .schema import envelope
    raw_data = (scene.data_payload.read_bytes()
                if isinstance(scene.data_payload, Path) else scene.data_payload)
    data = envelope.loads_envelope(raw_data, envelope.KIND_SCENE,
                                   schema_version=2, compact_arrays=True)
    raw_param = (scene.param_payload.read_bytes()
                 if isinstance(scene.param_payload, Path) else scene.param_payload)
    params = envelope.loads_envelope(raw_param, envelope.KIND_PARAM,
                                    schema_version=2)
    objects = {}
    for group in data:
        for obj in group["object"]:
            key = obj["uuid"]
            if key in objects:
                raise ValueError(f"duplicate export UUID: {key}")
            objects[key] = (group["type"], obj)
    bindings, cross, friction, materials = [], [], [], []
    for uuid, (kind, obj) in objects.items():
        tables = obj.pop("face_material_params", None)
        if tables:
            from ..materials.zones import validate_tables
            if kind != "SHELL":
                raise ValueError(f"{obj['name']}: Material Zones require Cloth/SHELL")
            validate_tables(obj['name'], tables, len(obj['face']))
            materials.append({"uuid": uuid, "faces": obj["face"], "params": tables})
        values = obj.pop("face_friction", None)
        if values is not None:
            if kind != "SHELL" or len(values) != len(obj["face"]):
                raise ValueError(f"{uuid}: invalid triangle friction binding")
            if any(not math.isfinite(v) or v < 0 for v in values):
                raise ValueError(f"{uuid}: invalid triangle friction value")
            friction.append({"uuid": uuid, "faces": obj["face"], "values": values})
    for entry in params.get("cross_stitch", []):
        source, target = entry["source_uuid"], entry["target_uuid"]
        if source != target:
            cross.append(entry)  # Official decoder owns cross/SOLID validation.
            continue
        if source not in objects:
            raise ValueError(f"intra seam references missing object: {source}")
        kind, obj = objects[source]
        # SOLID endpoints need upstream projection, not a guessed index map.
        if kind != "SHELL":
            raise ValueError(f"{source}: native intra seam requires a SHELL object")
        strength = float(entry["stitch_stiffness"])
        if not math.isfinite(strength) or strength < 0:
            raise ValueError(f"{source}: invalid seam stiffness")
        indices, weights = entry["ind"], entry["w"]
        if not indices or len(indices) != len(weights):
            raise ValueError(f"{source}: invalid seam row count")
        native = obj.setdefault("stitch", [[], []])
        for ind, w in zip(indices, weights):
            if (len(ind) != 6 or len(w) != 6
                    or ind[:3] != [ind[0]]*3 or w[:3] != [1.0, 0.0, 0.0]
                    or any(type(i) is not int or not 0 <= i < len(obj["vert"]) for i in ind)
                    or any(not math.isfinite(v) or v < 0 for v in w)
                    or abs(sum(w[3:])-1.0) > 1e-4):
                raise ValueError(f"{source}: unsupported or invalid intra seam row")
            native[0].append([ind[0], *ind[3:]])
            native[1].append([1.0, *w[3:]])
            bindings.append({"uuid": source, "ind": ind, "w": w,
                             "stiffness": strength})
    if cross:
        params["cross_stitch"] = cross
    else:
        params.pop("cross_stitch", None)
    binding_document = {"version": 1, "source_data_hash": scene.data_hash,
                           "source_param_hash": scene.param_hash,
                           "stitches": bindings, "face_friction": friction}
    if materials:
        binding_document["face_material_params"] = materials
    document = json.dumps(binding_document,
                          sort_keys=True, separators=(",", ":"), allow_nan=False)
    wire_data = envelope.dumps_envelope(envelope.KIND_SCENE, data, schema_version=2)
    wire_param = envelope.dumps_envelope(envelope.KIND_PARAM, params, schema_version=2)
    return replace(scene, data_payload=wire_data, param_payload=wire_param,
                   data_hash=envelope.payload_sha256(wire_data),
                   param_hash=envelope.payload_sha256(wire_param),
                   official_bridge_json=document)


def recovery_param_hash(scene) -> str:
    """Independent seam strengths remain part of the resume compatibility gate."""
    water=getattr(scene,'water_stream_json','')
    if water:
        document=json.dumps({'param_hash':scene.param_hash,
            'official_bridge':scene.official_bridge_json,'water_stream':json.loads(water)},
            sort_keys=True,separators=(',',':'))
        return hashlib.sha256(document.encode('utf-8')).hexdigest()
    if not scene.official_bridge_json:
        return scene.param_hash
    return hashlib.sha256(scene.official_bridge_json.encode("utf-8")).hexdigest()


def bind_stiffness(indices, weights, stiffness, mapping, bindings):
    """Match official reordered rows, without inferring object offsets."""
    import numpy as np
    indices = np.asarray(indices, dtype=np.uint64).reshape(-1, 6)
    weights = np.asarray(weights, dtype=np.float32).reshape(-1, 6)
    result = np.asarray(stiffness, dtype=np.float32).copy()
    if len(result) != len(indices) or indices.shape != weights.shape:
        raise ValueError("official stitch array shape mismatch")
    lookup = defaultdict(list)
    for ordinal, (ind, w) in enumerate(zip(indices, weights)):
        lookup[(tuple(ind), tuple(w))].append(ordinal)
    # Native intra rows were appended after existing native loose-edge rows.
    # Consume matches from the end, preserving both legacy and repeated seams.
    for row in reversed(bindings):
        vertices = mapping.get(row["uuid"])
        if vertices is None:
            raise ValueError(f"missing vertex map for intra seam: {row['uuid']}")
        try:
            ind = tuple(vertices[i] for i in row["ind"])
        except IndexError as exc:
            raise ValueError("intra seam map index out of range") from exc
        w = tuple(np.asarray(row["w"], dtype=np.float32))
        candidates = lookup.get((ind, w))
        if not candidates:
            raise ValueError("official build did not retain an authored intra seam row")
        result[candidates.pop()] = row["stiffness"]
    if not np.isfinite(result).all() or (result < 0).any():
        raise ValueError("invalid resolved stitch stiffness")
    return result


def bind_face_friction(triangles, values, mapping, bindings):
    """Resolve CNX face values onto the official triangle input ABI."""
    import numpy as np
    triangles = np.asarray(triangles, dtype=np.uint64).reshape(-1, 3)
    result = np.asarray(values, dtype=np.float32).copy()
    if len(result) != len(triangles):
        raise ValueError("official triangle material array shape mismatch")
    lookup = defaultdict(list)
    for ordinal, tri in enumerate(triangles):
        lookup[tuple(sorted(tri))].append(ordinal)
    for row in bindings:
        vertices = mapping.get(row["uuid"])
        if vertices is None:
            raise ValueError("missing vertex map for triangle friction")
        for face, value in zip(row["faces"], row["values"]):
            try:
                key = tuple(sorted(vertices[i] for i in face))
            except IndexError as exc:
                raise ValueError("triangle friction map index out of range") from exc
            candidates = lookup.get(key)
            if not candidates:
                raise ValueError("official build did not retain a painted triangle")
            result[candidates.pop(0)] = value
    if not np.isfinite(result).all() or (result < 0).any():
        raise ValueError("invalid resolved triangle friction")
    return result
