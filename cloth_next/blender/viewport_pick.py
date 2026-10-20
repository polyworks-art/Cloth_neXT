# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached object geometry, projections and occlusion tests for viewport tools.

Sewing and Object Attachment editors pick on the geometry the artist sees:
the evaluated mesh when it keeps the original topology (Mesh Cache playback,
deform modifiers), otherwise the original cage. Index data always refers to
the original mesh, so solver export is unaffected.
"""
from __future__ import annotations

import hashlib
import logging

import bpy
import numpy as np

from .. import sewing_pick
from ..attachments import topology_fingerprint

_log = logging.getLogger(__name__)

_geometry = {}
_data_users = {}
_version = 0
_fingerprints = {}


class ObjectGeometry:
    """World-space geometry of one object, rebuilt only after it changes."""

    def __init__(self, obj, depsgraph=None):
        global _version
        _version += 1
        self.version = _version
        self.pointer = obj.as_pointer()
        mesh = obj.data
        self.data_pointer = mesh.as_pointer()
        count = len(mesh.vertices)
        local = np.empty(count * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", local)
        matrix = np.array(obj.matrix_world, dtype=np.float64)
        self.rest = _transform(local.reshape(-1, 3), matrix)
        self.display = self.rest
        self.displayed_deformation = False
        if depsgraph is not None:
            try:
                evaluated = obj.evaluated_get(depsgraph)
                data = evaluated.data
                if (data is not None and data is not mesh
                        and len(data.vertices) == count and len(data.edges) == len(mesh.edges)):
                    shown = np.empty(count * 3, dtype=np.float64)
                    data.vertices.foreach_get("co", shown)
                    shown = _transform(shown.reshape(-1, 3),
                                       np.array(evaluated.matrix_world, dtype=np.float64))
                    if not np.allclose(shown, self.rest, atol=1e-7):
                        self.display = shown
                        self.displayed_deformation = True
            except (ReferenceError, RuntimeError, AttributeError):
                _log.debug("Evaluated mesh unavailable for %s", obj.name, exc_info=True)
        edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
        mesh.edges.foreach_get("vertices", edges)
        self.edges = edges.reshape(-1, 2)
        mesh.calc_loop_triangles()
        tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
        mesh.loop_triangles.foreach_get("vertices", tris)
        self.triangles_array = tris.reshape(-1, 3)
        # Boundary edges are used by exactly one face corner.
        loop_edges = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("edge_index", loop_edges)
        uses = np.bincount(loop_edges, minlength=len(self.edges)) if len(loop_edges) else (
            np.zeros(len(self.edges), dtype=np.int64))
        boundary = np.sort(self.edges[uses == 1], axis=1)
        self.boundary = tuple(map(tuple, boundary.tolist()))
        self.vertex_count = count
        self._rest_tuple = self._display_tuple = None
        self._edge_set = self._neighbours = self._boundary_neighbours = None
        self._boundary_vertices = None
        self._fingerprint = None
        self._bvh = None
        self._projection = {}

    @property
    def rest_tuple(self):
        if self._rest_tuple is None:
            self._rest_tuple = tuple(map(tuple, self.rest.tolist()))
        return self._rest_tuple

    @property
    def display_tuple(self):
        if self.display is self.rest:
            return self.rest_tuple
        if self._display_tuple is None:
            self._display_tuple = tuple(map(tuple, self.display.tolist()))
        return self._display_tuple

    @property
    def edge_tuples(self):
        return tuple(map(tuple, self.edges.tolist()))

    @property
    def triangles(self):
        return tuple(map(tuple, self.triangles_array.tolist()))

    @property
    def edge_set(self):
        if self._edge_set is None:
            self._edge_set = {(a, b) if a < b else (b, a) for a, b in self.edges.tolist()}
        return self._edge_set

    @property
    def neighbours(self):
        if self._neighbours is None:
            self._neighbours = sewing_pick.adjacency(self.vertex_count, self.edges.tolist())
        return self._neighbours

    @property
    def boundary_neighbours(self):
        if self._boundary_neighbours is None:
            self._boundary_neighbours = sewing_pick.adjacency(self.vertex_count, self.boundary)
        return self._boundary_neighbours

    @property
    def boundary_vertices(self):
        if self._boundary_vertices is None:
            self._boundary_vertices = {index for edge in self.boundary for index in edge}
        return self._boundary_vertices

    @property
    def fingerprint(self):
        """Solver-compatible topology fingerprint, memoised across deformation.

        The canonical fingerprint serialises every triangle; a cheap hash of
        the triangle array decides whether it must be recomputed after a
        transform or Mesh Cache frame change.
        """
        if self._fingerprint is None:
            quick = (self.vertex_count, hashlib.sha256(
                np.ascontiguousarray(self.triangles_array).tobytes()).hexdigest())
            cached = _fingerprints.get(quick)
            if cached is None:
                cached = topology_fingerprint(self.vertex_count, self.triangles)
                if len(_fingerprints) > 64:
                    _fingerprints.clear()
                _fingerprints[quick] = cached
            self._fingerprint = cached
        return self._fingerprint

    @property
    def bvh(self):
        if self._bvh is None:
            from mathutils.bvhtree import BVHTree
            self._bvh = BVHTree.FromPolygons(self.display.tolist(),
                                             self.triangles_array.tolist(),
                                             all_triangles=True, epsilon=0.0)
        return self._bvh

    def projection(self, view):
        """Projected display vertices for a ``ViewState``; one view is kept."""
        cached = self._projection.get(view.key)
        if cached is None:
            cached = {"points": sewing_pick.project(view.matrix, self.display,
                                                    view.width, view.height)}
            self._projection = {view.key: cached}
        return cached

    def edge_projection(self, view, kind):
        cached = self.projection(view)
        if kind not in cached:
            edges = self.boundary if kind == "boundary" else self.edges
            cached[kind] = sewing_pick.EdgeProjection(cached["points"], edges)
        return cached[kind]


def _transform(points, matrix):
    return points @ matrix[:3, :3].T + matrix[:3, 3]


class ViewState:
    def __init__(self, region, rv3d):
        matrix = rv3d.perspective_matrix
        self.matrix = np.array(matrix, dtype=np.float64)
        self.width, self.height = region.width, region.height
        self.region, self.rv3d = region, rv3d
        self.key = (region.as_pointer(), self.width, self.height,
                    tuple(np.round(self.matrix.ravel(), 9)))


def geometry(obj, depsgraph=None):
    key = obj.as_pointer()
    cached = _geometry.get(key)
    # A deleted object's address can be reused by a new one; a mesh can be
    # swapped or edited without an update reaching us (scripts, undo).
    if cached is not None and (cached.data_pointer != obj.data.as_pointer()
                               or cached.vertex_count != len(obj.data.vertices)
                               or len(cached.edges) != len(obj.data.edges)):
        cached = None
    if cached is None:
        if depsgraph is None:
            try:
                depsgraph = bpy.context.evaluated_depsgraph_get()
            except (AttributeError, RuntimeError):
                depsgraph = None
        cached = ObjectGeometry(obj, depsgraph)
        _geometry[key] = cached
        _data_users.setdefault(obj.data.as_pointer(), set()).add(key)
    return cached


def invalidate(obj=None):
    if obj is None:
        _geometry.clear()
        _data_users.clear()
        return
    _geometry.pop(obj.as_pointer(), None)


def invalidate_depsgraph(depsgraph):
    """Drop geometry for objects whose transform or mesh actually changed."""
    if not _geometry:
        return False
    changed = False
    for update in getattr(depsgraph, "updates", ()):
        if not (getattr(update, "is_updated_geometry", False)
                or getattr(update, "is_updated_transform", False)):
            continue
        identifier = getattr(update.id, "original", update.id)
        try:
            key = identifier.as_pointer()
        except ReferenceError:
            continue
        if _geometry.pop(key, None) is not None:
            changed = True
        for user in _data_users.pop(key, ()):
            changed = _geometry.pop(user, None) is not None or changed
    return changed


def is_visible(context, scene, point, origin, cloth_geometries, cloth_pointers, *, xray=False):
    """True when ``point`` is not hidden behind displayed geometry.

    Cloth occlusion uses the displayed cage of every candidate cloth, which
    contains the picked edge itself. Solidify or Subdivision shells therefore
    no longer hide their own boundary from one side. Other objects (bodies,
    colliders) occlude through a regular scene ray cast.
    """
    if xray:
        return True
    from mathutils import Vector
    point, origin = Vector(point), Vector(origin)
    ray = point - origin
    distance = ray.length
    if distance <= 1e-9:
        return True
    direction = ray / distance
    tolerance = max(1e-5, distance * 1e-3)
    for geometry_entry in cloth_geometries:
        hit = geometry_entry.bvh.ray_cast(origin, direction, distance - tolerance)
        if hit[0] is not None:
            return False
    depsgraph = context.evaluated_depsgraph_get()
    remaining, start = distance - tolerance, origin.copy()
    for _ in range(16):
        if remaining <= 0:
            return True
        hit, location, _normal, _index, obj, _matrix = scene.ray_cast(
            depsgraph, start, direction, distance=remaining)
        if not hit:
            return True
        original = getattr(obj, "original", obj)
        try:
            pointer = original.as_pointer()
        except ReferenceError:
            return True
        if pointer not in cloth_pointers:
            return False
        travelled = (location - start).length + tolerance * .5
        start = location + direction * (tolerance * .5)
        remaining -= travelled
    return True


def region_under_mouse(window, event):
    """(area, region, local mouse) for the 3D view WINDOW region under the mouse.

    Overlapping sidebars, tool shelves and headers return None so their UI
    keeps working while a modal editor runs.
    """
    try:
        areas = window.screen.areas
    except ReferenceError:
        return None
    x, y = event.mouse_x, event.mouse_y
    for area in areas:
        if area.type != "VIEW_3D":
            continue
        if not (area.x <= x < area.x + area.width and area.y <= y < area.y + area.height):
            continue
        window_region = None
        for region in area.regions:
            if not (region.x <= x < region.x + region.width
                    and region.y <= y < region.y + region.height):
                continue
            if region.type == "WINDOW":
                window_region = region
            elif region.width > 1 and region.height > 1:
                return None
        if window_region is not None:
            return area, window_region, (x - window_region.x, y - window_region.y)
    return None


def clear():
    invalidate()
