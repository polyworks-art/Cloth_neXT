# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Idempotent managed-frontend extensions owned by Cloth NeXt.

The pinned PPF core already consumes a friction value for every triangle,
while its Python frontend expands one object scalar across all triangles.
This narrowly scoped overlay preserves an optional ``face_friction`` scene
array and substitutes it during that existing expansion. It is applied only
to Cloth NeXt-managed solver installations, never external user installs.
"""

from __future__ import annotations

import os
from pathlib import Path

OVERLAY_VERSION = "face-friction-intersection-preview-v8"
UPSTREAM_013_RELEASE = "2026-07-26-22-53"

_DECODER_NEEDLE = '''                else:
                    _rust.validate_group_type(group_type)
                    _obj = None
'''
_DECODER_REPLACEMENT = _DECODER_NEEDLE + '''
                # Cloth NeXt extension: triangle-aligned Friction values.
                if _obj is not None and obj.get("face_friction") is not None:
                    import numpy as np
                    values = np.asarray(obj["face_friction"], dtype=np.float64)
                    if face is None or values.ndim != 1 or len(values) != len(face):
                        raise ValueError(
                            f"{name}: face_friction must contain one value per face"
                        )
                    if not np.all(np.isfinite(values)) or np.any(values < 0.0):
                        raise ValueError(f"{name}: face_friction values are invalid")
                    _obj._face_friction = values
'''

_SCENE_SIGNATURE = '''        def _extend_param(
            param: ParamHolder,
            concat_param: dict[str, list],
            count: int,
        ):
'''
_SCENE_SIGNATURE_REPLACEMENT = '''        def _extend_param(
            param: ParamHolder,
            concat_param: dict[str, list],
            count: int,
            per_element: dict[str, object] | None = None,
        ):
'''
_SCENE_EXTEND = '''                concat_param[key].extend([value] * count)
'''
_SCENE_EXTEND_REPLACEMENT = '''                override = (per_element or {}).get(key)
                if override is None:
                    concat_param[key].extend([value] * count)
                else:
                    values = np.asarray(override, dtype=np.float64).reshape(-1)
                    if len(values) != count:
                        raise ValueError(
                            f"per-element {key} count {len(values)} != {count}"
                        )
                    concat_param[key].extend(values.tolist())
'''
_SCENE_SHELL = '''            if tri_added and tet_added == 0:
                _extend_param(obj.param, concat_tri_param, tri_added)
'''
_SCENE_SHELL_REPLACEMENT = '''            if tri_added and tet_added == 0:
                face_friction = getattr(obj, "_face_friction", None)
                overrides = ({"friction": face_friction}
                             if face_friction is not None else None)
                _extend_param(obj.param, concat_tri_param, tri_added, overrides)
'''
_BUILD_WORKER_NEEDLE = '''                with open(os.path.join(root, "build_violations.json"), "w") as fp:
                    json.dump({"violations": violations}, fp)
'''
_BUILD_WORKER_REPLACEMENT = _BUILD_WORKER_NEEDLE + '''                data_root = os.environ.get("PPF_CTS_DATA_ROOT")
                if data_root:
                    mirror = os.path.join(
                        data_root, f"{name}.build_violations.json")
                    temporary = mirror + f".{os.getpid()}.tmp"
                    with open(temporary, "w") as fp:
                        json.dump({"violations": violations}, fp)
                    os.replace(temporary, mirror)
'''

_VIOLATION_NEEDLE = '''        all_violations = result["violations"]
        if all_violations:
            raise ValidationError(result["combined_message"], violations=all_violations)
'''
_VIOLATION_REPLACEMENT = '''        all_violations = result["violations"]
        # Cloth NeXt managed-frontend extension: the compiled preview builder
        # historically reconstructed entries from input.tri/dyn_verts and
        # silently dropped combined triangle indices belonging to statics.
        # Re-run the same authoritative Rust intersection kernel and construct
        # preview geometry from the exact combined buffers used for detection.
        if result.get("has_self_intersection"):
            from ._intersection_ import check_self_intersection
            dynamic_v = (
                np.asarray(self._vert[1], dtype=np.float64)
                + np.asarray(self._displacement, dtype=np.float64)[
                    np.asarray(self._vert[0], dtype=np.int64)])
            dynamic_f = np.asarray(self._tri, dtype=np.int32).reshape((-1, 3))
            if static_vert_for_check is not None and static_tri_for_check is not None:
                static_v = np.asarray(static_vert_for_check, dtype=np.float64)
                static_f = (
                    np.asarray(static_tri_for_check, dtype=np.int32)
                    + len(dynamic_v))
                combined_v = np.ascontiguousarray(
                    np.vstack((dynamic_v, static_v)), dtype=np.float64)
                combined_f = np.ascontiguousarray(
                    np.vstack((dynamic_f, static_f)), dtype=np.int32)
                combined_c = np.ascontiguousarray(
                    np.concatenate((
                        np.asarray(tri_is_collider, dtype=bool),
                        np.ones(len(static_f), dtype=bool))))
            else:
                combined_v = np.ascontiguousarray(dynamic_v, dtype=np.float64)
                combined_f = np.ascontiguousarray(dynamic_f, dtype=np.int32)
                combined_c = np.ascontiguousarray(tri_is_collider, dtype=bool)
            exact_pairs = check_self_intersection(
                combined_v, combined_f, combined_c,
                np.ascontiguousarray(self._rod, dtype=np.int32)
                if n_rods > 0 else None)
            combined_body = np.concatenate((
                np.asarray(tri_body_id, dtype=np.int32),
                np.zeros(len(combined_f) - len(dynamic_f), dtype=np.int32)))
            exact_pairs = [
                pair for pair in exact_pairs
                if (pair[0] < 0
                    or combined_body[pair[0]] == 0
                    or combined_body[pair[0]] != combined_body[pair[1]])
            ]
            preserved = [
                item for item in all_violations
                if not (isinstance(item, dict)
                        and item.get("type") == "self_intersection")
            ]
            original_intersections = [
                item for item in all_violations
                if (isinstance(item, dict)
                    and item.get("type") == "self_intersection")
            ]
            if not original_intersections:
                # The compiled kernel exposes its authoritative preview in a
                # separate field.  Some builds leave ``violations`` empty even
                # though ``has_self_intersection`` is true.
                for preview in result.get("self_intersections", ())[:100]:
                    if isinstance(preview, dict):
                        positions = preview.get("tri_positions", ())
                        is_rod = bool(preview.get("is_rod", False))
                    else:
                        positions = getattr(preview, "tri_positions", ())
                        is_rod = bool(getattr(preview, "is_rod", False))
                    positions = np.asarray(
                        positions, dtype=np.float64).reshape((-1, 3))
                    tris = [
                        positions[index:index + 3].tolist()
                        for index in range(0, len(positions), 3)
                        if len(positions[index:index + 3]) == 3
                    ]
                    if tris:
                        original_intersections.append({
                            "type": "self_intersection",
                            "classification": "SELF_INTERSECTION",
                            "is_rod": is_rod,
                            "tris": tris,
                        })
            if not exact_pairs:
                # The legacy assemble binding can set the summary flag while
                # exposing only an incomplete one-sided preview.  The public
                # Rust pair query above is the verifiable source of truth.
                # Without a confirmed pair neither the flag nor its partial
                # preview may block the Bake.
                self._has_self_intersection = False
                original_intersections = []
            exact = []
            for first, second in exact_pairs[:100]:
                elements = []
                for triangle_index in (first, second):
                    if triangle_index < 0:
                        continue
                    triangle = combined_f[int(triangle_index)]
                    elements.append({
                        "kind": "TRIANGLE",
                        "combined_triangle_index": int(triangle_index),
                        "vertices": combined_v[triangle].tolist(),
                    })
                exact.append({
                    "type": "self_intersection",
                    "classification": "SELF_INTERSECTION",
                    "combined_pair": [int(first), int(second)],
                    "is_rod": bool(first < 0),
                    "elements": elements,
                    # Backward-compatible geometry for existing consumers.
                    "tris": [element["vertices"] for element in elements],
                })
            # Never discard the solver's authoritative violation geometry.
            # Some managed builds report a validation hit here while the
            # separately exported pair query returns no indices.  The legacy
            # entry still contains the offending triangle and is sufficient
            # for Cloth NeXt to highlight that face.
            all_violations = preserved + (
                exact if exact else original_intersections)
            if (not self._has_self_intersection
                    and not self._has_contact_offset_violation
                    and not self._has_wall_violation
                    and not self._has_sphere_violation):
                # ``result["violations"]`` in legacy bindings may retain an
                # opaque one-sided preview under an unknown type name.  Once
                # every validation flag is clear it is stale by definition
                # and must not raise ValidationError below.
                all_violations = []
        if all_violations:
            raise ValidationError(result["combined_message"], violations=all_violations)
'''
_VIOLATION_REPLACEMENT_V6 = _VIOLATION_REPLACEMENT.replace(
    '''            if (not self._has_self_intersection
                    and not self._has_contact_offset_violation
                    and not self._has_wall_violation
                    and not self._has_sphere_violation):
                # ``result["violations"]`` in legacy bindings may retain an
                # opaque one-sided preview under an unknown type name.  Once
                # every validation flag is clear it is stale by definition
                # and must not raise ValidationError below.
                all_violations = []
''', "")
_VIOLATION_REPLACEMENT_V5 = _VIOLATION_REPLACEMENT_V6.replace(
    '''            if not exact_pairs:
                # The legacy assemble binding can set the summary flag while
                # exposing only an incomplete one-sided preview.  The public
                # Rust pair query above is the verifiable source of truth.
                # Without a confirmed pair neither the flag nor its partial
                # preview may block the Bake.
                self._has_self_intersection = False
                original_intersections = []
''', '''            if not exact_pairs and not original_intersections:
                # The legacy assemble binding can set the summary flag while
                # exposing neither a pair nor preview geometry.  The public
                # Rust pair query above is the verifiable source of truth:
                # without a confirmed pair there is no actionable face and
                # this inconsistent flag must not block the Bake.
                self._has_self_intersection = False
''')
_VIOLATION_REPLACEMENT_V4 = _VIOLATION_REPLACEMENT_V5.replace(
    '''            if not exact_pairs and not original_intersections:
                # The legacy assemble binding can set the summary flag while
                # exposing neither a pair nor preview geometry.  The public
                # Rust pair query above is the verifiable source of truth:
                # without a confirmed pair there is no actionable face and
                # this inconsistent flag must not block the Bake.
                self._has_self_intersection = False
''', "")
_VIOLATION_REPLACEMENT_V3 = _VIOLATION_REPLACEMENT_V4.replace(
    '''            if not original_intersections:
                # The compiled kernel exposes its authoritative preview in a
                # separate field.  Some builds leave ``violations`` empty even
                # though ``has_self_intersection`` is true.
                for preview in result.get("self_intersections", ())[:100]:
                    if isinstance(preview, dict):
                        positions = preview.get("tri_positions", ())
                        is_rod = bool(preview.get("is_rod", False))
                    else:
                        positions = getattr(preview, "tri_positions", ())
                        is_rod = bool(getattr(preview, "is_rod", False))
                    positions = np.asarray(
                        positions, dtype=np.float64).reshape((-1, 3))
                    tris = [
                        positions[index:index + 3].tolist()
                        for index in range(0, len(positions), 3)
                        if len(positions[index:index + 3]) == 3
                    ]
                    if tris:
                        original_intersections.append({
                            "type": "self_intersection",
                            "classification": "SELF_INTERSECTION",
                            "is_rod": is_rod,
                            "tris": tris,
                        })
''', "")
_VIOLATION_REPLACEMENT_V2 = _VIOLATION_REPLACEMENT_V3.replace(
    '''            original_intersections = [
                item for item in all_violations
                if (isinstance(item, dict)
                    and item.get("type") == "self_intersection")
            ]
''', "").replace(
    '''            # Never discard the solver's authoritative violation geometry.
            # Some managed builds report a validation hit here while the
            # separately exported pair query returns no indices.  The legacy
            # entry still contains the offending triangle and is sufficient
            # for Cloth NeXt to highlight that face.
            all_violations = preserved + (
                exact if exact else original_intersections)
''',
    '''            all_violations = preserved + exact
''')


class SolverOverlayError(RuntimeError):
    pass


def _replace_once(path: Path, replacements: tuple[tuple[str, str], ...]) -> None:
    text = path.read_text(encoding="utf-8")
    updated = text
    for needle, replacement in replacements:
        if replacement in updated:
            continue
        if updated.count(needle) != 1:
            raise SolverOverlayError(
                f"{path.name}: expected exactly one compatible patch location")
        updated = updated.replace(needle, replacement, 1)
    if updated == text:
        return
    temporary = path.with_suffix(path.suffix + ".cloth-next.tmp")
    temporary.write_text(updated, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _upgrade_violation_overlay(path: Path) -> None:
    """Upgrade an already patched managed frontend without solver changes."""
    text = path.read_text(encoding="utf-8")
    if _VIOLATION_REPLACEMENT in text:
        return
    previous = next((
        candidate for candidate in (
            _VIOLATION_REPLACEMENT_V6,
            _VIOLATION_REPLACEMENT_V5,
            _VIOLATION_REPLACEMENT_V4,
            _VIOLATION_REPLACEMENT_V3, _VIOLATION_REPLACEMENT_V2)
        if text.count(candidate) == 1), None)
    if previous is None:
        return
    updated = text.replace(previous, _VIOLATION_REPLACEMENT, 1)
    temporary = path.with_suffix(path.suffix + ".cloth-next.tmp")
    temporary.write_text(updated, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def apply_managed_solver_overlay(bundle_root: Path) -> None:
    root = Path(bundle_root)
    frontend = root / "frontend"
    marker = root / f".cloth-next-{OVERLAY_VERSION}"
    if marker.is_file():
        return
    decoder = frontend / "_decoder_.py"
    scene = frontend / "_scene_.py"
    build_worker = frontend / "build_worker.py"
    if (not decoder.is_file() or not scene.is_file()
            or not build_worker.is_file()):
        raise SolverOverlayError("managed solver frontend files are missing")
    _upgrade_violation_overlay(scene)
    _replace_once(decoder, ((_DECODER_NEEDLE, _DECODER_REPLACEMENT),))
    _replace_once(scene, (
        (_SCENE_SIGNATURE, _SCENE_SIGNATURE_REPLACEMENT),
        (_SCENE_EXTEND, _SCENE_EXTEND_REPLACEMENT),
        (_SCENE_SHELL, _SCENE_SHELL_REPLACEMENT),
        (_VIOLATION_NEEDLE, _VIOLATION_REPLACEMENT),
    ))
    _replace_once(build_worker, (
        (_BUILD_WORKER_NEEDLE, _BUILD_WORKER_REPLACEMENT),))
    marker.write_text(OVERLAY_VERSION + "\n", encoding="ascii")


def apply_solver_overlay(bundle_root: Path, *, protocol_version: str,
                         schema_version: str,
                         official_release_tag: str | None,
                         managed: bool) -> None:
    """Apply only the exact integration recipe verified for this release."""
    if not managed:
        return
    identity = (protocol_version, schema_version, official_release_tag)
    verified_upstream = {
        ("0.13", "2", UPSTREAM_013_RELEASE): (
            ".cloth-next-upstream-integration-0.13-schema-2",
            (),
        ),
        ("0.18", "2", "2026-08-12-15-47"): (
            ".cloth-next-upstream-integration-0.18-schema-2",
            (
                'elif key == "lock-translation":',
                'elif key == "lock-rotation":',
                'elif key == "lock-rotation-prohibit-axis":',
                'statistics_input_path = os.path.join(path, "statistics_input.cbor")',
            ),
        ),
    }
    recipe = verified_upstream.get(identity)
    if recipe is not None:
        frontend = bundle_root / "frontend"
        scene = frontend / "_scene_.py"
        decoder = frontend / "_decoder_.py"
        worker = frontend / "build_worker.py"
        if not scene.is_file() or not decoder.is_file() or not worker.is_file():
            raise SolverOverlayError(
                f"verified protocol {protocol_version} frontend files are missing")
        scene_text = scene.read_text(encoding="utf-8")
        decoder_text = decoder.read_text(encoding="utf-8")
        worker_text = worker.read_text(encoding="utf-8")
        common_required = (
            'all_violations = result["violations"]',
            'raise ValidationError(result["combined_message"], violations=all_violations)',
            'json.dump({"violations": violations}, fp)',
        )
        sources = (scene_text, scene_text, worker_text)
        if any(source.count(anchor) != 1
               for source, anchor in zip(sources, common_required)):
            raise SolverOverlayError(
                f"protocol {protocol_version} upstream integration anchors "
                "do not match the verified release")
        marker_name, protocol_anchors = recipe
        if any((scene_text.count(anchor) + decoder_text.count(anchor)
                + worker_text.count(anchor)) != 1
               for anchor in protocol_anchors):
            raise SolverOverlayError(
                f"protocol {protocol_version} frontend contract does not match the "
                "verified release")
        marker = bundle_root / marker_name
        marker.write_text(str(official_release_tag) + "\n", encoding="ascii")
        return
    raise SolverOverlayError(
        "no Cloth NeXt integration is registered for "
        f"protocol={protocol_version}, schema={schema_version}, "
        f"release={official_release_tag!r}")
