# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""CNX-owned subprocess using an unchanged official frontend and input ABI.

Never install this in the solver tree. Run it with that release's Python.
Native triangle input ABI: official FixedScene.export_fixed writes uint64
tri.bin and float32 bin/param/tri-friction.bin; the official solver checks
one value per triangle. This edits scene INPUT DATA, not frontend code.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Loaded by absolute file path because bundled Python may ignore PYTHONPATH.
# Relative package imports stay compatible with Blender's extension namespace.
import importlib.util

_spec = importlib.util.spec_from_file_location("cnx_official_scene_bridge", Path(__file__).with_name("official_scene_bridge.py"))
_bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bridge)
bind_face_friction, bind_stiffness = _bridge.bind_face_friction, _bridge.bind_stiffness


def apply_bindings(project_name, project_root, document):
    import cbor2
    import numpy as np
    from frontend import App

    if not re.fullmatch(r"[A-Za-z0-9_-]+", project_name):
        raise ValueError("unsafe project identity")
    if document.get("version") != 1:
        raise ValueError("unsupported CNX binding version")
    if os.environ.get("PPF_CTS_DATA_ROOT"):
        raise ValueError("test-rig overrides are prohibited for official integration")
    directory = Path(project_root).resolve(strict=True) / "session"
    if not (directory / "fixed_session.pickle").is_file():
        raise ValueError("official saved session is missing")
    # App.recover documents this pointer format, including Windows fallback.
    pointer = Path(App.get_data_dirpath()) / "symlinks" / (project_name + ".txt")
    pointer.parent.mkdir(parents=True, exist_ok=True)
    target = str(directory)
    created = False
    try:
        try:
            with pointer.open("x", encoding="utf-8") as stream:
                stream.write(target)
            created = True
        except FileExistsError:
            if pointer.read_text(encoding="utf-8").strip() != target:
                raise ValueError("saved-session pointer belongs to another project")
        session = App.recover(project_name)
        if Path(session.info.path).resolve() != directory:
            raise ValueError("official recovery resolved a different project")
        mapping = cbor2.loads((directory / "map.pickle").read_bytes())
        if mapping["kind"] != "VertexMap" or mapping["version"] != 2:
            raise ValueError("unexpected official vertex map format")
        mapping = mapping["payload"]
        binary = directory / "bin"
        if document["stitches"]:
            ind = np.fromfile(binary / "stitch_ind.bin", dtype=np.uint64).reshape(-1, 6)
            w = np.fromfile(binary / "stitch_w.bin", dtype=np.float32).reshape(-1, 6)
            stiffness = np.fromfile(binary / "stitch_stiffness.bin", dtype=np.float32)
            resolved = bind_stiffness(ind, w, stiffness, mapping, document["stitches"])
            session.fixed_scene.set_stitch(ind, w, stiffness=resolved)
            # Save the graph through the public API; never edit pickle internals.
            session = session.session.build(preserve_output=True)
            check = App.recover(project_name)
            np.testing.assert_array_equal(
                np.fromfile(binary / "stitch_stiffness.bin", dtype=np.float32), resolved)
            assert Path(check.info.path).resolve() == directory
        if document["face_friction"]:
            path = binary / "param" / "tri-friction.bin"
            tri = np.fromfile(binary / "tri.bin", dtype=np.uint64).reshape(-1, 3)
            values = np.fromfile(path, dtype=np.float32)
            resolved = bind_face_friction(tri, values, mapping, document["face_friction"])
            temporary = path.with_suffix(".cnx-tmp")
            resolved.tofile(temporary)
            os.replace(temporary, path)
        return {"stitch_rows": len(document["stitches"]),
                "painted_objects": len(document["face_friction"])}
    finally:
        if created and pointer.is_file() and pointer.read_text(encoding="utf-8").strip() == target:
            pointer.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--bindings", required=True, type=Path)
    args = parser.parse_args()
    document = json.loads(args.bindings.read_text(encoding="utf-8"))
    print(json.dumps(apply_bindings(args.project_name, args.project_root, document)), flush=True)


if __name__ == "__main__":
    main()
