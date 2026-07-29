# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Animation-aware rigid character collision cages.

The visible character mesh is evaluated across the requested bake range once.
Vertices are grouped by deform bone, transformed back into that bone's local
space and reduced to deterministic support points. Each bone receives one
conservative convex hull driven by a Copy Transforms constraint. PPF therefore
sees ordinary transform-only STATIC colliders instead of a full deforming mesh
sample at every motion sample.
"""

from __future__ import annotations

from dataclasses import dataclass

import bpy
import numpy as np

CAGE_SEGMENT_MARKER = "cloth_next_character_cage_segment"
CAGE_PRIMARY_MARKER = "cloth_next_character_cage_primary"
CAGE_BONE = "cloth_next_character_cage_bone"


class CharacterCageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CharacterCageResult:
    primary: object
    segments: tuple[object, ...]
    source_vertices: int
    result_vertices: int
    sampled_frames: tuple[int, ...]


def is_cage_segment(obj) -> bool:
    try:
        return bool(obj.get(CAGE_SEGMENT_MARKER, False))
    except (AttributeError, TypeError):
        return False


def is_primary_cage_segment(obj) -> bool:
    try:
        return bool(obj.get(CAGE_PRIMARY_MARKER, False))
    except (AttributeError, TypeError):
        return False


def sample_frames(frame_start: int, frame_end: int, step: int) -> tuple[int, ...]:
    start = int(frame_start)
    end = int(frame_end)
    if end < start:
        raise CharacterCageError(
            "Character Cage Bake End must not precede Bake Start.")
    stride = max(1, int(step))
    frames = list(range(start, end + 1, stride))
    if frames[-1] != end:
        frames.append(end)
    return tuple(frames)


def _all_objects() -> tuple[object, ...]:
    objects = getattr(getattr(bpy, "data", None), "objects", ())
    try:
        return tuple(objects)
    except TypeError:
        values = getattr(objects, "values", None)
        return tuple(values()) if values is not None else ()


def owned_cage_segments(source) -> tuple[object, ...]:
    found = []
    for obj in _all_objects():
        if not is_cage_segment(obj):
            continue
        settings = getattr(obj, "cloth_next", None)
        if (settings is not None and
                getattr(settings, "collider_proxy_source", None) is source):
            found.append(obj)
    found.sort(key=lambda item: str(
        getattr(item, "name_full", getattr(item, "name", ""))))
    return tuple(found)


def remove_owned_character_cage(source) -> None:
    for obj in owned_cage_segments(source):
        mesh = getattr(obj, "data", None)
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh is not None and getattr(mesh, "users", 1) == 0:
            bpy.data.meshes.remove(mesh)


def cage_vertex_count(source) -> int:
    return sum(len(getattr(getattr(obj, "data", None), "vertices", ()))
               for obj in owned_cage_segments(source))


def cage_segment_count(source) -> int:
    return len(owned_cage_segments(source))


def validate_character_cage(source):
    primary = getattr(source.cloth_next, "collider_proxy_object", None)
    segments = owned_cage_segments(source)
    if (primary is None or primary not in segments or
            not is_primary_cage_segment(primary)):
        raise CharacterCageError(
            f"{source.name}: Character Collision Cage is enabled, but its "
            "primary segment is missing. Regenerate or disable the Proxy.")
    if any(getattr(segment, "type", "") != "MESH" for segment in segments):
        raise CharacterCageError(
            f"{source.name}: Character Collision Cage contains a non-Mesh "
            "segment. Regenerate it.")
    return primary


def sync_character_cage_settings(source) -> None:
    source_settings = source.cloth_next
    for segment in owned_cage_segments(source):
        target = segment.cloth_next
        target.enabled = True
        target.role = "COLLIDER"
        target.collider_motion = "ANIMATED"
        target.collider_capture_mode = "TRANSFORM_ONLY"
        target.collider_samples_per_frame = int(
            source_settings.collider_samples_per_frame)
        target.bake_start = int(source_settings.bake_start)
        target.bake_end = int(source_settings.bake_end)
        target.collider_proxy_enabled = False
        target.collider_proxy_object = None
        target.collider_proxy_source = source
        for name in ("surface_grip", "collision_gap", "surface_offset"):
            setattr(target.collision, name,
                    getattr(source_settings.collision, name))


def _find_armature(source):
    armatures = []
    for modifier in getattr(source, "modifiers", ()):
        if (getattr(modifier, "type", "") == "ARMATURE" and
                bool(getattr(modifier, "show_viewport", True))):
            target = getattr(modifier, "object", None)
            if target is not None and getattr(target, "type", "") == "ARMATURE":
                armatures.append(target)
    unique = []
    for armature in armatures:
        if armature not in unique:
            unique.append(armature)
    if len(unique) != 1:
        raise CharacterCageError(
            f"{source.name}: Character Collision Cage requires exactly one "
            "enabled Armature modifier with an assigned Armature object.")
    return unique[0]


def _bone_vertex_indices(source, armature, threshold: float,
                         minimum_vertices: int) -> dict[str, np.ndarray]:
    deform_names = {
        bone.name for bone in getattr(armature.data, "bones", ())
        if bool(getattr(bone, "use_deform", True))
    }
    group_names = {
        group.index: group.name for group in source.vertex_groups
        if group.name in deform_names
    }
    assigned: dict[str, list[int]] = {name: [] for name in deform_names}
    for vertex in source.data.vertices:
        influences = []
        for member in vertex.groups:
            name = group_names.get(member.group)
            if name is not None and float(member.weight) > 0.0:
                influences.append((float(member.weight), name))
        if not influences:
            continue
        selected = [name for weight, name in influences
                    if weight >= threshold]
        if not selected:
            selected = [max(influences)[1]]
        for name in set(selected):
            assigned[name].append(int(vertex.index))
    minimum = max(4, int(minimum_vertices))
    return {
        name: np.asarray(indices, dtype=np.int32)
        for name, indices in assigned.items() if len(indices) >= minimum
    }


def _support_directions() -> np.ndarray:
    directions = []
    for x in (-1.0, 0.0, 1.0):
        for y in (-1.0, 0.0, 1.0):
            for z in (-1.0, 0.0, 1.0):
                if x == y == z == 0.0:
                    continue
                vector = np.asarray((x, y, z), dtype=np.float64)
                directions.append(vector / np.linalg.norm(vector))
    return np.asarray(directions, dtype=np.float64)


def _matrix_array(matrix) -> np.ndarray:
    result = np.asarray([
        [float(matrix[row][column]) for column in range(4)]
        for row in range(4)
    ], dtype=np.float64)
    if result.shape != (4, 4) or not np.isfinite(result).all():
        raise CharacterCageError(
            "Character Cage encountered an invalid transform matrix.")
    return result


def _transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    homogeneous = np.ones((len(points), 4), dtype=np.float64)
    homogeneous[:, :3] = points
    return (homogeneous @ matrix.T)[:, :3]


def _capture_support_points(context, source, armature,
                            assignments: dict[str, np.ndarray],
                            frames: tuple[int, ...]) -> dict[str, np.ndarray]:
    directions = _support_directions()
    collected: dict[str, list[np.ndarray]] = {
        name: [] for name in assignments
    }
    expected_vertices = len(source.data.vertices)
    for frame in frames:
        context.scene.frame_set(frame)
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = source.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            if len(mesh.vertices) != expected_vertices:
                raise CharacterCageError(
                    f"{source.name}: evaluated Character topology changes at "
                    f"frame {frame}. Character Cage requires stable vertex order.")
            local = np.empty((expected_vertices, 3), dtype=np.float64)
            mesh.vertices.foreach_get("co", local.reshape(-1))
            world = _transform_points(
                local, _matrix_array(evaluated.matrix_world))
            for bone_name, indices in assignments.items():
                pose_bone = armature.pose.bones.get(bone_name)
                if pose_bone is None:
                    continue
                bone_world = _matrix_array(
                    armature.matrix_world @ pose_bone.matrix)
                try:
                    world_to_bone = np.linalg.inv(bone_world)
                except np.linalg.LinAlgError as exc:
                    raise CharacterCageError(
                        f"{source.name}: bone {bone_name!r} has a singular "
                        f"transform at frame {frame}.") from exc
                points = _transform_points(world[indices], world_to_bone)
                dots = points @ directions.T
                support_indices = np.unique(np.argmax(dots, axis=0))
                collected[bone_name].append(points[support_indices])
        finally:
            evaluated.to_mesh_clear()
    result = {}
    for bone_name, chunks in collected.items():
        if not chunks:
            continue
        points = np.concatenate(chunks, axis=0)
        points = np.unique(np.round(points, decimals=7), axis=0)
        if len(points) >= 4:
            result[bone_name] = points
    return result


def _proxy_collection(scene):
    from . import collider_proxy

    collection = bpy.data.collections.get(collider_proxy.PROXY_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(collider_proxy.PROXY_COLLECTION)
        scene.collection.children.link(collection)
    return collection


def _hull_mesh(name: str, points: np.ndarray, bone_length: float,
               margin: float, joint_overlap: float):
    import bmesh

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        for point in points:
            bm.verts.new(tuple(float(value) for value in point))
        bm.verts.ensure_lookup_table()
        result = bmesh.ops.convex_hull(
            bm, input=list(bm.verts), use_existing_faces=False)
        disposable = []
        for key in ("geom_unused", "geom_interior"):
            disposable.extend(
                item for item in result.get(key, ())
                if isinstance(item, bmesh.types.BMVert) and item.is_valid)
        if disposable:
            bmesh.ops.delete(
                bm, geom=list(set(disposable)), context="VERTS")
        if len(bm.faces) < 4:
            raise CharacterCageError(
                f"{name}: could not form a closed convex hull.")
        length = max(float(bone_length), 1e-5)
        overlap = max(0.0, float(joint_overlap))
        if overlap:
            center_y = length * 0.5
            scale_y = (length + 2.0 * overlap) / length
            for vertex in bm.verts:
                vertex.co.y = center_y + (
                    vertex.co.y - center_y) * scale_y
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.normal_update()
        thickness = max(0.0, float(margin))
        if thickness:
            for vertex in bm.verts:
                vertex.co += vertex.normal * thickness
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
        mesh.update()
        return mesh
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    finally:
        bm.free()


def _create_segment(context, source, armature, bone_name: str,
                    points: np.ndarray, primary: bool):
    from . import collider_proxy

    pose_bone = armature.pose.bones.get(bone_name)
    if pose_bone is None:
        raise CharacterCageError(
            f"{source.name}: bone {bone_name!r} disappeared.")
    safe_name = bone_name.replace("/", "_").replace("\\", "_")
    mesh = _hull_mesh(
        f"{source.data.name}_CNX_Cage_{safe_name}", points,
        float(getattr(pose_bone, "length", 0.0)),
        float(source.cloth_next.collider_cage_margin),
        float(source.cloth_next.collider_cage_joint_overlap))
    obj = bpy.data.objects.new(
        f"{source.name}_CNX_Cage_{safe_name}", mesh)
    _proxy_collection(context.scene).objects.link(obj)
    obj.display_type = "WIRE"
    obj.hide_render = True
    obj.show_name = False
    obj[CAGE_SEGMENT_MARKER] = True
    obj[CAGE_PRIMARY_MARKER] = bool(primary)
    obj[CAGE_BONE] = bone_name
    if primary:
        obj[collider_proxy.PROXY_MARKER] = True
    constraint = obj.constraints.new("COPY_TRANSFORMS")
    constraint.name = "Cloth NeXt Character Cage Bone"
    constraint.target = armature
    constraint.subtarget = bone_name
    constraint.target_space = "WORLD"
    constraint.owner_space = "WORLD"
    if hasattr(constraint, "mix_mode"):
        constraint.mix_mode = "REPLACE"
    settings = obj.cloth_next
    settings.enabled = True
    settings.role = "COLLIDER"
    settings.collider_motion = "ANIMATED"
    settings.collider_capture_mode = "TRANSFORM_ONLY"
    settings.collider_samples_per_frame = int(
        source.cloth_next.collider_samples_per_frame)
    settings.bake_start = int(source.cloth_next.bake_start)
    settings.bake_end = int(source.cloth_next.bake_end)
    settings.collider_proxy_enabled = False
    settings.collider_proxy_object = None
    settings.collider_proxy_source = source
    for field in ("surface_grip", "collision_gap", "surface_offset"):
        setattr(settings.collision, field,
                getattr(source.cloth_next.collision, field))
    return obj


def generate_character_cage(context, source) -> CharacterCageResult:
    settings = source.cloth_next
    if (getattr(source, "type", "") != "MESH" or not settings.enabled or
            settings.role != "COLLIDER" or
            settings.collider_motion != "ANIMATED"):
        raise CharacterCageError(
            "Character Collision Cage requires an enabled, animated Mesh "
            "Collider.")
    armature = _find_armature(source)
    frames = sample_frames(
        settings.bake_start, settings.bake_end,
        settings.collider_cage_sample_step)
    assignments = _bone_vertex_indices(
        source, armature,
        float(settings.collider_cage_weight_threshold),
        int(settings.collider_cage_min_vertices))
    if not assignments:
        raise CharacterCageError(
            f"{source.name}: no deform bone owns enough weighted vertices to "
            "build a Character Collision Cage.")
    original_frame = int(context.scene.frame_current)
    created = []
    try:
        support = _capture_support_points(
            context, source, armature, assignments, frames)
        if not support:
            raise CharacterCageError(
                f"{source.name}: animation sampling produced no usable bone "
                "hulls.")
        remove_owned_character_cage(source)
        primary_bone = max(
            support, key=lambda name: len(assignments[name]))
        for bone_name in sorted(support):
            created.append(_create_segment(
                context, source, armature, bone_name,
                support[bone_name], bone_name == primary_bone))
        primary = next(
            item for item in created if is_primary_cage_segment(item))
        settings.collider_proxy_object = primary
        settings.collider_proxy_enabled = True
        settings.collider_proxy_source_vertices = len(source.data.vertices)
        settings.collider_proxy_result_vertices = sum(
            len(item.data.vertices) for item in created)
        return CharacterCageResult(
            primary, tuple(created), len(source.data.vertices),
            int(settings.collider_proxy_result_vertices), frames)
    except Exception:
        for obj in tuple(created):
            if getattr(obj, "name", "") in bpy.data.objects:
                mesh = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh is not None and getattr(mesh, "users", 1) == 0:
                    bpy.data.meshes.remove(mesh)
        raise
    finally:
        context.scene.frame_set(original_frame)
        context.view_layer.update()
