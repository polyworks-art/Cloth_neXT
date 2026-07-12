# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Developer-only deterministic PPF test scene (Phase 3A).

Creates the shared vertical-slice fixture (one 11x11 cloth grid above one
static UV-sphere collider) inside a dedicated "Cloth NeXt Test" collection.
The geometry comes from :mod:`cloth_next.ppf_run.fixture` — the exact meshes
the standalone harness simulates — so the Blender run and the harness run
are the same scene. Never deletes user data; recreating an existing test
collection requires explicit confirmation.
"""

from __future__ import annotations

import bpy

from ..ppf_run import fixture


def _test_collection(context) -> bpy.types.Collection | None:
    return bpy.data.collections.get(fixture.TEST_COLLECTION_NAME)


def _remove_collection(collection: bpy.types.Collection) -> None:
    for obj in list(collection.objects):
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(mesh, bpy.types.Mesh) and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    bpy.data.collections.remove(collection)


def _create_mesh_object(mesh_fixture: fixture.FixtureMesh, role: str,
                        collection: bpy.types.Collection) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(mesh_fixture.name)
    mesh.from_pydata([list(v) for v in mesh_fixture.vertices_local], [],
                     [list(t) for t in mesh_fixture.triangles])
    mesh.validate()
    mesh.update()
    obj = bpy.data.objects.new(mesh_fixture.name, mesh)
    obj.location = mesh_fixture.world_translation
    collection.objects.link(obj)
    obj.cloth_next.enabled = True
    obj.cloth_next.role = role
    return obj


class CLOTHNEXT_OT_create_test_scene(bpy.types.Operator):
    """Create the deterministic Cloth NeXt PPF test scene (developer tool)"""

    bl_idname = "clothnext.create_test_scene"
    bl_label = "Create PPF Test Scene"
    bl_options = {"INTERNAL", "UNDO"}

    replace_existing: bpy.props.BoolProperty(
        name="Replace Existing Test Collection", default=False,
        options={"SKIP_SAVE"})

    def invoke(self, context, _event):
        if _test_collection(context) is not None:
            # Explicit confirmation before touching the existing collection.
            self.replace_existing = True
            return context.window_manager.invoke_confirm(
                self, _event,
                title="Replace the existing 'Cloth NeXt Test' collection?",
                message="Its generated objects will be removed and recreated.")
        return self.execute(context)

    def execute(self, context):
        existing = _test_collection(context)
        if existing is not None:
            if not self.replace_existing:
                self.report({"WARNING"},
                            "The 'Cloth NeXt Test' collection already exists; "
                            "confirm replacement to recreate it.")
                return {"CANCELLED"}
            _remove_collection(existing)
        collection = bpy.data.collections.new(fixture.TEST_COLLECTION_NAME)
        context.scene.collection.children.link(collection)
        cloth_fixture, collider_fixture = fixture.vertical_slice_fixture()
        cloth = _create_mesh_object(cloth_fixture, "CLOTH", collection)
        _create_mesh_object(collider_fixture, "COLLIDER", collection)
        scene = context.scene
        scene.frame_start = fixture.FRAME_START
        scene.frame_end = fixture.FRAME_END
        scene.frame_set(fixture.FRAME_START)
        scene.gravity = fixture.DEFAULT_GRAVITY
        scene.use_gravity = True
        context.view_layer.objects.active = cloth
        self.report({"INFO"},
                    f"Created {fixture.CLOTH_NAME} (121 vertices) and "
                    f"{fixture.COLLIDER_NAME}; frames "
                    f"{fixture.FRAME_START}-{fixture.FRAME_END}.")
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_create_test_scene,)
