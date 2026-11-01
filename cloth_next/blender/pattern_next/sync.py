# SPDX-License-Identifier: GPL-3.0-or-later
"""Pattern -> Blender mesh synchronisation (main thread only).

Each pattern owns exactly one generated mesh object, a real Cloth NeXt
Cloth object in the "PATTERN NeXt" collection. Remeshing rewrites only that
object's mesh datablock in place, so modifiers, materials, the Cloth NeXt
settings and the object's 3D arrangement transform survive every edit.

Mesh-local coordinates are pattern-local ``(u, v) -> (u, 0, v)``: the
panel's rest shape. The canvas placement (2D position/rotation) only feeds
the UV map (UV = canvas metres), never the object transform.

Edits are validated by meshing *before* anything is written: a failed edit
leaves the previous pattern and its mesh untouched.
"""

from __future__ import annotations

import logging
import time

import bpy
import numpy as np

from ... import export_identity
from ...pattern_next.mesher import mesh_shape
from ...pattern_next.seams import SeamError, SeamSide, side_vertices
from ...pattern_next.shapes import Contour, PatternShape, to_canvas
from ...pattern_next.validation import PatternError
from .. import validation_state
from . import model

_log = logging.getLogger(__name__)

COLLECTION_NAME = "PATTERN NeXt"
COLLECTION_MARKER = "cloth_next_pattern_collection"
OBJECT_MARKER = "cloth_next_pattern_id"
UV_NAME = "PatternUV"

# identifier -> (shape key, MeshResult); rebuilt deterministically when absent.
_results = {}
_guard = set()
timings = {}


def clear_cache():
    _results.clear()


# -- triangulation backend ----------------------------------------------------

def blender_cdt(points, edges, outline=None):
    """Blender's exact constrained Delaunay, mapped back to input indices.

    With ``outline`` the CDT keeps only triangles inside that face (output
    type 1). This matters on convex outline edges: samples of a slanted
    straight edge are not exactly collinear in floating point, and a full
    triangulation (type 0) fills the gap to the convex hull with
    zero-area slivers. Holes are removed afterwards by the mesher.
    """
    from mathutils import Vector
    from mathutils.geometry import delaunay_2d_cdt
    verts = [Vector((float(x), float(y))) for x, y in points]
    faces_in = [list(map(int, outline))] if outline is not None else []
    out_verts, _edges, faces, orig_verts, _orig_edges, _orig_faces = delaunay_2d_cdt(
        verts, [tuple(map(int, e)) for e in edges], faces_in, 1 if faces_in else 0, 1e-9, True)
    if len(out_verts) != len(verts):
        raise PatternError("TRIANGULATION_FAILED",
                           "Constraint edges intersect; the outline is not simple")
    mapping = np.full(len(out_verts), -1, dtype=np.int64)
    for out_index, originals in enumerate(orig_verts):
        if len(originals) != 1:
            raise PatternError("TRIANGULATION_FAILED", "Two pattern points coincide")
        mapping[out_index] = originals[0]
    triangles = np.asarray([f for f in faces if len(f) == 3], dtype=np.int64).reshape(-1, 3)
    return mapping[triangles]


blender_cdt.inside_only = True  # output type 1: nothing outside the outline


def mesh(shape: PatternShape):
    return mesh_shape(shape, blender_cdt)


# -- objects ------------------------------------------------------------------

def collection(scene):
    for item in scene.collection.children_recursive:
        if item.get(COLLECTION_MARKER):
            return item
    item = bpy.data.collections.new(COLLECTION_NAME)
    item[COLLECTION_MARKER] = 1
    scene.collection.children.link(item)
    return item


def object_alive(scene, pattern):
    obj = pattern.mesh_object
    try:
        return obj is not None and obj.type == "MESH" and scene.objects.get(obj.name) == obj
    except ReferenceError:
        return False


def _enable_cloth(obj):
    """Make the panel a regular Cloth NeXt Cloth object (same as Add Physics)."""
    from ..playback_cache import ensure_simulation_modifier
    export_identity.ensure_persistent_id(obj)
    settings = obj.cloth_next
    if not settings.enabled:
        settings.enabled = True
    if settings.role != "CLOTH":
        settings.role = "CLOTH"
    ensure_simulation_modifier(obj)


def ensure_object(scene, pattern):
    if object_alive(scene, pattern):
        return pattern.mesh_object
    data = bpy.data.meshes.new(pattern.name)
    obj = bpy.data.objects.new(pattern.name, data)
    obj[OBJECT_MARKER] = pattern.identifier
    settings = scene.cloth_next_pattern_settings
    # Staging area: the panel appears where it was drawn, facing the front.
    obj.location = (pattern.location[0], settings.staging_offset, pattern.location[1])
    collection(scene).objects.link(obj)
    pattern.mesh_object = obj
    if pattern.material is not None:
        data.materials.append(pattern.material)
    return obj


def write_mesh(obj, result, pattern):
    started = time.perf_counter()
    data = obj.data
    vertices = result.vertices
    triangles = result.triangles
    n, m = len(vertices), len(triangles)
    data.clear_geometry()
    data.vertices.add(n)
    co = np.zeros((n, 3), dtype=np.float32)
    co[:, 0], co[:, 2] = vertices[:, 0], vertices[:, 1]
    data.vertices.foreach_set("co", co.ravel())
    data.loops.add(m * 3)
    data.loops.foreach_set("vertex_index", triangles.astype(np.int32).ravel())
    data.polygons.add(m)
    data.polygons.foreach_set("loop_start", np.arange(0, m * 3, 3, dtype=np.int32))
    data.update(calc_edges=True)
    data.validate(verbose=False)
    _write_uvs(data, pattern)
    data.update()
    timings["write_mesh"] = time.perf_counter() - started


def _write_uvs(data, pattern):
    layer = data.uv_layers.get(UV_NAME) or data.uv_layers.new(name=UV_NAME)
    if layer is None:
        return
    data.uv_layers.active = layer
    count = len(data.vertices)
    co = np.empty(count * 3, dtype=np.float32)
    data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    (lx, ly), angle = model.placement(pattern)
    c, s = np.cos(angle), np.sin(angle)
    u = co[:, 0] * c - co[:, 2] * s + lx
    v = co[:, 0] * s + co[:, 2] * c + ly
    loops = np.empty(len(data.loops), dtype=np.int32)
    data.loops.foreach_get("vertex_index", loops)
    uv = np.stack((u[loops], v[loops]), axis=1).astype(np.float32)
    layer.data.foreach_set("uv", uv.ravel())


def update_uvs(scene, pattern):
    if object_alive(scene, pattern):
        _write_uvs(pattern.mesh_object.data, pattern)
        pattern.mesh_object.data.update()


# -- synchronisation ------------------------------------------------------------

def result_for(scene, pattern):
    """Cached mesher result for the pattern's current shape."""
    shape = model.shape(pattern)
    key = shape.key()
    cached = _results.get(pattern.identifier)
    if cached is not None and cached[0] == key:
        return cached[1]
    result = mesh(shape)
    _results[pattern.identifier] = (key, result)
    return result


def _matches(obj, result):
    try:
        return (len(obj.data.vertices) == len(result.vertices)
                and len(obj.data.polygons) == len(result.triangles))
    except (ReferenceError, AttributeError):
        return False


def rebuild(scene, pattern, *, force=False, seams=True):
    """Bring the pattern's mesh object up to date. Raises PatternError."""
    started = time.perf_counter()
    shape = model.shape(pattern)
    key = shape.key()
    cached = _results.get(pattern.identifier)
    if cached is not None and cached[0] == key and not force and object_alive(scene, pattern) \
            and _matches(pattern.mesh_object, cached[1]):
        return cached[1]
    result = mesh(shape)
    meshed = time.perf_counter()
    obj = ensure_object(scene, pattern)
    write_mesh(obj, result, pattern)
    if not obj.cloth_next.enabled or obj.cloth_next.role != "CLOTH":
        _enable_cloth(obj)
    _results[pattern.identifier] = (key, result)
    pattern.vertex_count = int(result.stats["vertices"])
    pattern.triangle_count = int(result.stats["triangles"])
    pattern.area = float(result.stats["area"])
    pattern.status = ""
    pattern.revision += 1
    pattern["_pn_good"] = (float(pattern.particle_distance), *pattern.primitive_size)
    total = time.perf_counter() - started
    pattern.mesh_time = total
    timings.update(mesh=meshed - started, total=total, **{
        k: v for k, v in result.stats.items() if k.startswith("time_")})
    validation_state.mark_all_settings_dirty()
    if seams:
        sync_seams(scene, {pattern.identifier})
    return result


def commit(scene, pattern, points=None, contours=None, *, seams=True):
    """Validate a candidate outline by meshing it, then write it.

    ``contours`` is an optional list of (kind, uid, points). Returns
    (True, "") or (False, message); on failure nothing is changed.
    """
    candidate = model.shape(pattern)
    if points is not None:
        candidate = PatternShape(Contour(tuple(points)), candidate.holes, candidate.lines,
                                 candidate.spacing, candidate.tolerance,
                                 candidate.location, candidate.rotation)
    if contours is not None:
        candidate = PatternShape(
            candidate.outline,
            tuple(Contour(tuple(p)) for kind, _uid, p in contours if kind == "HOLE"),
            tuple(Contour(tuple(p), False) for kind, _uid, p in contours if kind == "LINE"),
            candidate.spacing, candidate.tolerance, candidate.location, candidate.rotation)
    try:
        result = mesh(candidate)
    except PatternError as exc:
        return False, str(exc)
    if points is not None:
        model.write_points(pattern.points, points)
    if contours is not None:
        while len(pattern.contours):
            pattern.contours.remove(0)
        for kind, uid, contour_points in contours:
            item = pattern.contours.add()
            item.kind, item.uid = kind, uid
            model.write_points(item.points, contour_points)
    _results[pattern.identifier] = (candidate.key(), result)
    rebuild(scene, pattern, force=True, seams=seams)
    return True, ""


def request(pattern):
    """RNA update hook (Particle Distance, primitive size): remesh or revert."""
    if pattern.identifier in _guard or pattern.get("_pn_suspend"):
        return
    scene = _scene_of(pattern)
    if scene is None:
        return
    _guard.add(pattern.identifier)
    try:
        rebuild(scene, pattern)
        pattern["_pn_good"] = (float(pattern.particle_distance), *pattern.primitive_size)
    except PatternError as exc:
        good = pattern.get("_pn_good")
        if good is not None:
            pattern.particle_distance = good[0]
            pattern.primitive_size = good[1:3]
            model.apply_primitive(pattern)
        pattern.status = str(exc)
        _log.info("PATTERN NeXt: %s", exc)
    finally:
        _guard.discard(pattern.identifier)
    from . import draw
    draw.tag_redraw()


def placement_changed(pattern):
    if pattern.get("_pn_suspend"):
        return
    scene = _scene_of(pattern)
    if scene is not None:
        update_uvs(scene, pattern)
    from . import draw
    draw.tag_redraw()


def assign_material(pattern):
    scene = _scene_of(pattern)
    if scene is None or not object_alive(scene, pattern):
        return
    data = pattern.mesh_object.data
    if pattern.material is None:
        data.materials.clear()
    elif len(data.materials):
        data.materials[0] = pattern.material
    else:
        data.materials.append(pattern.material)


def rename_object(pattern):
    scene = _scene_of(pattern)
    if scene is None or not object_alive(scene, pattern):
        return
    pattern.mesh_object.name = pattern.name
    pattern.mesh_object.data.name = pattern.name


def _scene_of(pattern):
    owner = pattern.id_data
    return owner if isinstance(owner, bpy.types.Scene) else None


def remove_pattern(scene, pattern):
    identifier = pattern.identifier
    for index in reversed(range(len(scene.cloth_next_pattern_seams))):
        seam = scene.cloth_next_pattern_seams[index]
        if identifier in (seam.a_pattern, seam.b_pattern):
            remove_seam(scene, index)
    obj = pattern.mesh_object
    if obj is not None:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)
    index = model.index_of(scene, pattern)
    if index >= 0:
        scene.cloth_next_patterns.remove(index)
    _results.pop(identifier, None)


def ensure_all(scene):
    """After load/undo: recreate panels whose object was deleted in 3D."""
    for pattern in scene.cloth_next_patterns:
        if not object_alive(scene, pattern):
            try:
                rebuild(scene, pattern, force=True)
            except PatternError as exc:
                pattern.status = str(exc)


# -- seams -> existing Cloth NeXt sewing definitions ------------------------------

def _sewing_row(scene, identifier):
    for index, item in enumerate(scene.cloth_next_sewing_definitions):
        if identifier and item.identifier == identifier:
            return index, item
    return -1, None


def derive_seam(scene, seam):
    """(Re)create the Cloth NeXt sewing definition for a pattern seam."""
    from .. import sewing
    pa, pb = model.find(scene, seam.a_pattern), model.find(scene, seam.b_pattern)
    index, _row = _sewing_row(scene, seam.sewing_identifier)
    if index >= 0:
        scene.cloth_next_sewing_definitions.remove(index)
    if pa is None or pb is None:
        seam.status = "A sewn pattern was deleted"
        return False
    try:
        ra, rb = result_for(scene, pa), result_for(scene, pb)
        side_a = side_vertices(ra.outline, SeamSide(pa.identifier, seam.a_start, seam.a_end))
        side_b = side_vertices(rb.outline, SeamSide(pb.identifier, seam.b_start, seam.b_end))
        if not object_alive(scene, pa) or not object_alive(scene, pb):
            raise SeamError("A sewn panel has no 3D mesh")
        item = sewing._commit(scene, pa.mesh_object, pb.mesh_object, side_a, side_b,
                              flipped=bool(seam.flipped))
    except (SeamError, PatternError, ValueError) as exc:
        seam.status = str(exc)
        return False
    if not seam.sewing_identifier:
        seam.sewing_identifier = item.identifier
    item.identifier = seam.sewing_identifier
    item.name = seam.name
    seam.status = ""
    return True


def sync_seams(scene, identifiers=None):
    for seam in scene.cloth_next_pattern_seams:
        if identifiers is None or seam.a_pattern in identifiers or seam.b_pattern in identifiers:
            derive_seam(scene, seam)


def request_seam(seam):
    scene = seam.id_data
    if isinstance(scene, bpy.types.Scene) and not seam.get("_pn_suspend"):
        derive_seam(scene, seam)
        from . import draw
        draw.tag_redraw()


def remove_seam(scene, index):
    seam = scene.cloth_next_pattern_seams[index]
    row, _item = _sewing_row(scene, seam.sewing_identifier)
    if row >= 0:
        scene.cloth_next_sewing_definitions.remove(row)
    scene.cloth_next_pattern_seams.remove(index)


def canvas_of(pattern, local):
    location, rotation = model.placement(pattern)
    return to_canvas(local, location, rotation)
