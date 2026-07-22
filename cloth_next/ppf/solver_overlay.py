# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Idempotent Cloth NeXt frontend extension for per-face shell friction.

The pinned PPF core already consumes a friction value for every triangle,
while its Python frontend expands one object scalar across all triangles.
This narrowly scoped overlay preserves an optional ``face_friction`` scene
array and substitutes it during that existing expansion. It is applied only
to Cloth NeXt-managed solver installations, never external user installs.
"""

from __future__ import annotations

import os
from pathlib import Path

OVERLAY_VERSION = "face-friction-v1"

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


def apply_managed_solver_overlay(bundle_root: Path) -> None:
    root = Path(bundle_root)
    frontend = root / "frontend"
    marker = root / f".cloth-next-{OVERLAY_VERSION}"
    if marker.is_file():
        return
    decoder = frontend / "_decoder_.py"
    scene = frontend / "_scene_.py"
    if not decoder.is_file() or not scene.is_file():
        raise SolverOverlayError("managed solver frontend files are missing")
    _replace_once(decoder, ((_DECODER_NEEDLE, _DECODER_REPLACEMENT),))
    _replace_once(scene, (
        (_SCENE_SIGNATURE, _SCENE_SIGNATURE_REPLACEMENT),
        (_SCENE_EXTEND, _SCENE_EXTEND_REPLACEMENT),
        (_SCENE_SHELL, _SCENE_SHELL_REPLACEMENT),
    ))
    marker.write_text(OVERLAY_VERSION + "\n", encoding="ascii")
