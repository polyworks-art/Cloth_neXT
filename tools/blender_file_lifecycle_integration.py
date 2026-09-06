# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender file/scene/object lifecycle regression for Cloth NeXt."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(values)


def main() -> None:
    args = _args()
    sys.path.insert(0, str(args.repo))
    from cloth_next import export_identity
    from cloth_next.bake import pc2
    from cloth_next.blender import (registration, solver_test, validation_state,
                                    viewport_colors)

    registration.register()
    try:
        viewport_load_handler = (
            viewport_colors._on_load_post)
        assert bpy.app.handlers.load_post.count(viewport_load_handler) == 1
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_plane_add()
        cloth = bpy.context.object
        cloth.name = "Cloth ä ö 한글 ! #"
        cloth.cloth_next.enabled = True
        cloth.cloth_next.role = "CLOTH"
        export_identity.ensure_persistent_id(cloth)
        cloth_uuid = export_identity.export_uuid(cloth)

        bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, -1.0))
        collider = bpy.context.object
        collider.name = "Collider ü spaces (test)"
        collider.cloth_next.enabled = True
        collider.cloth_next.role = "COLLIDER"
        export_identity.ensure_persistent_id(
            collider, occupied=(cloth.cloth_next.persistent_export_id,))
        collider_uuid = export_identity.export_uuid(collider)

        second = bpy.data.scenes.new("Scene Zwei – 테스트")
        bpy.context.window.scene = second
        bpy.context.window.scene = bpy.data.scenes[0]
        bpy.data.scenes.remove(second)

        # Blender deliberately disables undo at background startup and its
        # operators require an interactive editor context. Do not force that
        # context: it is not a supported headless lifecycle operation.
        undo_redo = False
        undo_redo_reason = "Blender background mode has no undo editor context"

        with tempfile.TemporaryDirectory(prefix="cloth next lifecycle ") as raw:
            blend = Path(raw) / "Überprüfung 한글 file.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
            assert blend.is_file()
            bpy.ops.wm.open_mainfile(filepath=str(blend))
            assert bpy.app.handlers.load_post.count(viewport_load_handler) == 1
            cloth = bpy.data.objects["Cloth ä ö 한글 ! #"]
            collider = bpy.data.objects["Collider ü spaces (test)"]
            assert export_identity.export_uuid(cloth) == cloth_uuid
            assert export_identity.export_uuid(collider) == collider_uuid
            collider.name = "Renamed Collider 한글"
            bpy.data.objects.remove(collider, do_unlink=True)
            cloth.name = "Renamed Cloth ä"
            bpy.context.view_layer.update()
            bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(blend))
            assert bpy.app.handlers.load_post.count(viewport_load_handler) == 1
            assert "Renamed Cloth ä" in bpy.data.objects
            assert "Renamed Collider 한글" not in bpy.data.objects
            assert export_identity.export_uuid(
                bpy.data.objects["Renamed Cloth ä"]) == cloth_uuid
            cloth = bpy.data.objects["Renamed Cloth ä"]
            bpy.context.view_layer.objects.active = cloth
            cloth.select_set(True)
            artist_path = Path(raw) / "artist mesh cache 한글.pc2"
            owned_path = Path(raw) / "cn_test_cloth_owned.pc2"
            artist_path.write_bytes(b"artist")
            owned_path.write_bytes(b"owned")
            artist = cloth.modifiers.new("Cloth NeXt Test Cache", "MESH_CACHE")
            artist.filepath = str(artist_path)
            artist_name = artist.name
            owned = cloth.modifiers.new("Owned playback", "MESH_CACHE")
            owned.filepath = str(owned_path)
            solver_test.mark_owned_playback(cloth, owned, str(owned_path))
            assert not solver_test.is_cloth_next_playback_modifier(cloth, artist)
            assert solver_test.is_cloth_next_playback_modifier(cloth, owned)

            # Export/validation temporarily excludes only the owned playback
            # modifier and restores its exact viewport/render state even when
            # the body exits normally.
            owned.show_viewport = True
            owned.show_render = False
            with solver_test.without_owned_playback(cloth) as disabled:
                assert disabled == (owned,)
                assert not owned.show_viewport
                assert not owned.show_render
                assert artist.show_viewport
            assert owned.show_viewport
            assert not owned.show_render

            # Exercise the production retarget path.  Keeping the old file
            # open on Windows reproduces the deletion failure caused by a
            # consumer retaining a cache handle: attaching the new valid PC2
            # must still commit, reuse the modifier, and leave the locked old
            # cache for later cleanup.
            replacement_path = Path(raw) / "cn_test_cloth_retarget.pc2"
            initial_local = tuple(tuple(vertex.co) for vertex in cloth.data.vertices)
            header = pc2.write_pc2(replacement_path, (initial_local,))
            plan = SimpleNamespace(
                deformables=(), initial_local=initial_local,
                world_matrix=tuple(tuple(row) for row in cloth.matrix_world),
                cloth_object_name=cloth.name, work_directory=Path(raw),
                pc2_path=replacement_path, frame_count=1, frame_start=1,
                material_meta={}, deformable_role="CLOTH",
                settings_fingerprint="", geometry_fingerprint="",
                topology_signature="", scene=SimpleNamespace(
                    cloth_uuid=cloth_uuid))
            with owned_path.open("rb") as locked_old_cache:
                assert locked_old_cache.read(1) == b"o"
                solver_test._attach_playback(plan, header)
                assert owned in cloth.modifiers[:]
                assert owned.filepath == str(replacement_path)
                assert solver_test.is_cloth_next_playback_modifier(cloth, owned)
                if sys.platform == "win32":
                    assert owned_path.exists()

            assert bpy.ops.clothnext.solver_test_clear() == {"FINISHED"}
            assert cloth.modifiers.get(artist_name) is not None
            assert artist_path.read_bytes() == b"artist"
            assert owned not in cloth.modifiers[:]
            assert not replacement_path.exists()
            windows_lock_preserved_old_cache = owned_path.exists()
            owned_path.unlink(missing_ok=True)

        report = {
            "blender": bpy.app.version_string,
            "cloth_uuid": cloth_uuid,
            "collider_uuid": collider_uuid,
            "handlers_while_registered": validation_state.handler_count(),
            "unicode_save_reopen": True,
            "scene_switch_delete": True,
            "undo_redo": undo_redo,
            "undo_redo_reason": undo_redo_reason,
            "rename_delete_reopen": True,
            "artist_cache_preserved": True,
            "owned_playback_disable_restore": True,
            "owned_playback_retarget": True,
            "clear_removed_retargeted_cache": True,
            "windows_lock_preserved_old_cache": (
                windows_lock_preserved_old_cache),
        }
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("CLOTH_NEXT_FILE_LIFECYCLE_PASS", json.dumps(report))
    finally:
        registration.unregister()
        assert validation_state.handler_count() == 0
        assert viewport_load_handler not in bpy.app.handlers.load_pre


if __name__ == "__main__":
    main()
