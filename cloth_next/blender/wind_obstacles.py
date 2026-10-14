# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluated surface queries for local Wind, with one disposable frame cache."""
from dataclasses import dataclass
import hashlib
import json
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from ..gaia.wind_obstacles import ObstacleSamples
from ..gaia.wind_field import sample_points

_cache = {}
_surface_cache = {}
_sample_cache = {}
_building = False


def objects(context, mode, *, include_targets=False):
    if mode == 'NONE':
        return ()
    result = []
    for obj in context.scene.objects:
        if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
            continue
        settings = getattr(obj, 'cloth_next', None)
        enabled = bool(settings is not None and settings.enabled)
        # Avoid self-shielding and feedback from the Cloth Bake cache: these
        # objects are targets of this air field, not prescribed obstacles.
        if not include_targets and enabled and settings.role in {'CLOTH', 'SOFT_BODY', 'ROD', 'RIGID_BODY'}:
            continue
        if mode == 'COLLIDERS' and not (enabled and settings.role == 'COLLIDER'):
            continue
        result.append(obj)
    return tuple(sorted(result, key=lambda obj: obj.name_full))


def candidates(context, mode, *, include_targets=False):
    result = [(obj, None, ('object', obj.name_full), None)
              for obj in objects(context, mode, include_targets=include_targets)]
    if mode == 'SCENE':
        for instance in context.evaluated_depsgraph_get().object_instances:
            if not instance.is_instance or instance.object.type not in {'MESH','CURVE','SURFACE','FONT','META'}:
                continue
            obj = instance.object.original
            settings = getattr(obj, 'cloth_next', None)
            if not include_targets and settings is not None and settings.enabled and settings.role in {'CLOTH','SOFT_BODY','ROD','RIGID_BODY'}:
                continue
            parent = instance.parent.original if instance.parent is not None else None
            name = parent.name_full if parent is not None else ''
            identity = ('instance', obj.name_full, name, tuple(instance.persistent_id))
            result.append((obj, instance.matrix_world.copy(), identity, parent))
    return tuple(result)


def invalidate(*_args, frame_only=False, geometry_ids=None):
    if not _building:
        _cache.clear()
        if not frame_only:
            if geometry_ids is None:
                _surface_cache.clear()
            else:
                for key in tuple(_surface_cache):
                    if key[0] in geometry_ids or key[1] in geometry_ids:
                        del _surface_cache[key]
            _sample_cache.clear()


@dataclass(frozen=True)
class Surface:
    tree: object
    center: np.ndarray
    scale: float
    normal_sign: float
    closed: bool
    minimum: np.ndarray
    maximum: np.ndarray


class WorldTree:
    """Exact world-space queries on a local BVH under a uniform transform."""
    def __init__(self, tree, matrix, scale):
        self.tree = tree
        self.matrix = matrix.copy()
        self.inverse = matrix.inverted()
        self.normal = self.inverse.to_3x3().transposed()
        self.sign = -1. if matrix.determinant() < 0 else 1.
        self.scale = scale

    def _result(self, hit):
        point, normal, face, distance = hit
        if point is None:
            return hit
        normal = (self.normal @ normal).normalized() * self.sign
        return self.matrix @ point, normal, face, distance * self.scale

    def find_nearest(self, point, distance=float('inf')):
        return self._result(self.tree.find_nearest(self.inverse @ point, distance/self.scale))

    def ray_cast(self, point, direction, distance):
        local_direction = (self.inverse.to_3x3() @ direction).normalized()
        return self._result(self.tree.ray_cast(self.inverse @ point,local_direction,distance/self.scale))


class MeshSampler:
    def __init__(self, surfaces, cancel=None):
        self.surfaces, self.cancel = tuple(surfaces), cancel

    def sample(self, points, direction, reach):
        if self.cancel is not None:
            self.cancel()
        digest = hashlib.sha256()
        for values in (points, direction, reach):
            digest.update(np.asarray(values, dtype='<f8').tobytes())
        key = (tuple(id(surface) for surface in self.surfaces), digest.digest())
        if key in _sample_cache:
            return _sample_cache[key][1]
        count = len(points)
        distance = np.full(count, np.inf); normal = np.zeros((count, 3))
        center = np.zeros((count, 3)); scale = np.ones(count)
        inside = np.zeros(count, bool); blocked = np.full(count, np.inf)
        hit_scale = np.ones(count); hit_center = np.zeros((count, 3))
        backwards = Vector(tuple(-direction))
        ray_points = points - direction * 1e-6
        for surface in self.surfaces:
            # Vectorized AABB tests avoid Python/BVH queries for irrelevant
            # objects. Exact nearest/ray tests remain unchanged after pruning.
            delta = np.maximum(0., np.maximum(surface.minimum-points, points-surface.maximum))
            nearest_mask = np.sum(delta*delta,axis=1) <= distance*distance
            ray_min = np.zeros(count); ray_max = np.asarray(reach).copy()
            ray_mask = reach > 1e-6
            for axis in range(3):
                component = -direction[axis]
                if abs(component) < 1e-12:
                    ray_mask &= (ray_points[:,axis] >= surface.minimum[axis]) & (ray_points[:,axis] <= surface.maximum[axis])
                else:
                    a = (surface.minimum[axis]-ray_points[:,axis])/component
                    b = (surface.maximum[axis]-ray_points[:,axis])/component
                    ray_min = np.maximum(ray_min,np.minimum(a,b))
                    ray_max = np.minimum(ray_max,np.maximum(a,b))
            ray_mask &= ray_min <= ray_max
            for iteration, index in enumerate(np.flatnonzero(nearest_mask | ray_mask)):
                if self.cancel is not None and iteration % 128 == 0:
                    self.cancel()
                point = points[index]; origin = Vector(tuple(point))
                if nearest_mask[index]:
                    nearest, face_normal, _face, d = surface.tree.find_nearest(origin)
                else:
                    nearest = None
                if nearest is not None:
                    n = np.asarray(face_normal) * surface.normal_sign
                    if surface.closed and float((point - np.asarray(nearest)) @ n) < -1e-6:
                        inside[index] = True
                    if d < distance[index]:
                        distance[index] = d; normal[index] = n
                        center[index] = surface.center; scale[index] = surface.scale
                if not ray_mask[index]:
                    continue
                hit, _n, _face, hit_distance = surface.tree.ray_cast(
                    origin + backwards * 1e-6, backwards, float(reach[index]))
                if hit is not None and hit_distance < blocked[index]:
                    blocked[index] = max(0., hit_distance)
                    hit_scale[index] = surface.scale; hit_center[index] = surface.center
        result = ObstacleSamples(distance, normal, center, scale, inside, blocked, hit_scale, hit_center)
        # Bound retained query buffers; Surface objects remain strongly held
        # by the entry, preventing recycled identity keys from matching.
        if count <= 16384:
            if len(_sample_cache) >= 8:
                _sample_cache.pop(next(iter(_sample_cache)))
            _sample_cache[key] = (self.surfaces, result)
        return result


def sampler(context, mode, minimum, maximum, *, cached=False, cancel=None, include_targets=False):
    global _building
    if mode == 'NONE':
        return MeshSampler((), cancel)
    stale = [key for key in _surface_cache if key[3] is not None
             and (key[3],key[4]) != (context.scene.frame_current,context.scene.frame_subframe)]
    for key in stale:
        del _surface_cache[key]
    if stale:
        _sample_cache.clear()
    include_targets = include_targets and mode == 'SCENE'
    selected = candidates(context, mode, include_targets=include_targets)
    key = (context.scene.as_pointer(), context.scene.frame_current,
           context.scene.frame_subframe, mode, tuple(minimum), tuple(maximum),
           include_targets, tuple((obj.as_pointer(), identity,
             tuple(value for row in matrix for value in row) if matrix is not None else None)
             for obj, matrix, identity, _parent in selected))
    if cached and key in _cache:
        return MeshSampler(_cache[key], cancel)
    from . import solver_test
    surfaces = []
    lo, hi = np.asarray(minimum), np.asarray(maximum)
    _building = True
    try:
        for obj, instance_matrix, _identity, _parent in selected:
            if cancel is not None:
                cancel()
            evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
            matrix = instance_matrix if instance_matrix is not None else evaluated.matrix_world
            bounds = np.asarray([tuple(matrix @ Vector(corner)) for corner in evaluated.bound_box])
            if np.any(bounds.max(axis=0) < lo) or np.any(bounds.min(axis=0) > hi):
                continue
            data = getattr(obj, 'data', None)
            # Animated/deformed geometry keeps a frame-specific key. Static
            # meshes reuse their tree across frames and export/preview modes.
            dynamic = bool(obj.modifiers or getattr(data,'shape_keys',None))
            surface_key = (obj.as_pointer(), data.as_pointer() if data else 0,
                tuple(value for row in matrix for value in row),
                context.scene.frame_current if dynamic else None,
                context.scene.frame_subframe if dynamic else None,
                include_targets and getattr(getattr(obj,'cloth_next',None),'role',None) in {'CLOTH','SOFT_BODY','ROD','RIGID_BODY'},cached)
            if surface_key in _surface_cache:
                surfaces.append(_surface_cache[surface_key])
                continue
            # Preview target locations may follow an attached Bake cache;
            # obstacle physics always uses the prescribed input surface.
            settings = getattr(obj, 'cloth_next', None)
            target = include_targets and settings is not None and settings.enabled and settings.role in {'CLOTH','SOFT_BODY','ROD','RIGID_BODY'}
            from contextlib import nullcontext
            boundaries = solver_test.simulation_modifiers(obj)
            # Scene-only geometry has no simulation boundary. A Collider's
            # boundary is a passthrough; if no visible modifiers follow it,
            # its evaluated mesh already equals the prescribed input mesh.
            direct = target or not boundaries
            if boundaries and settings is not None and settings.role == 'COLLIDER':
                boundary_index = list(obj.modifiers).index(boundaries[0])
                direct = not any(modifier.show_viewport for modifier in list(obj.modifiers)[boundary_index+1:])
            evaluation = nullcontext(obj) if direct else solver_test._evaluate_through_solver_input_modifiers(context, obj)
            with evaluation as boundary:
                evaluated = (boundary or obj).evaluated_get(context.evaluated_depsgraph_get())
                matrix = instance_matrix if instance_matrix is not None else evaluated.matrix_world.copy()
                mesh = evaluated.to_mesh()
                try:
                    mesh.calc_loop_triangles()
                    if not len(mesh.loop_triangles):
                        continue
                    linear = np.asarray(matrix,float)[:3,:3]
                    gram = linear.T @ linear
                    uniform_scale = float(np.sqrt(max(0.,np.trace(gram)/3)))
                    uniform = uniform_scale > 1e-12 and np.allclose(gram,np.eye(3)*uniform_scale**2,rtol=1e-6,atol=1e-12*uniform_scale**2)
                    native_tree = None
                    if uniform and cached and evaluated.type == 'MESH':
                        try:
                            native_tree = BVHTree.FromObject(evaluated,context.evaluated_depsgraph_get())
                        except (ValueError,RuntimeError):
                            pass
                    if native_tree is not None:
                        # Build in Blender's C code rather than copying all
                        # mesh coordinates/triangles into Python lists.
                        tree = WorldTree(native_tree,matrix,uniform_scale)
                        edge_indices = np.empty(len(mesh.loops),dtype=np.int32)
                        mesh.loops.foreach_get('edge_index',edge_indices)
                        counts = np.bincount(edge_indices,minlength=len(mesh.edges))
                        closed = bool(len(mesh.polygons) and np.all(counts[counts>0] == 2))
                        world_bounds = np.asarray([tuple(matrix @ Vector(corner)) for corner in evaluated.bound_box])
                        minimum,maximum = world_bounds.min(axis=0),world_bounds.max(axis=0)
                        if len(mesh.loop_triangles):
                            surface = Surface(tree,(minimum+maximum)*.5,
                                max(.01,float(np.linalg.norm(maximum-minimum))*.25),
                                -1. if matrix.determinant()<0 else 1.,closed,minimum,maximum)
                            surfaces.append(surface)
                            if len(_surface_cache)>=64:
                                _surface_cache.pop(next(iter(_surface_cache))); _sample_cache.clear()
                            _surface_cache[surface_key]=surface
                        continue
                    vertices = np.empty((len(mesh.vertices), 3), dtype=np.float64)
                    mesh.vertices.foreach_get('co', vertices.reshape(-1))
                    triangles = np.empty((len(mesh.loop_triangles), 3), dtype=np.int32)
                    mesh.loop_triangles.foreach_get('vertices', triangles.reshape(-1))
                finally:
                    evaluated.to_mesh_clear()
            if not len(triangles):
                continue
            transform = np.asarray(matrix, float)
            # A three-column mesh transform is faster without dispatching a
            # threaded BLAS job (especially on large animated characters).
            vertices = np.einsum('ij,kj->ik', vertices, transform[:3,:3], optimize=False) + transform[:3,3]
            if not np.isfinite(vertices).all():
                raise solver_test.SceneValidationError(f'{obj.name}: invalid Wind obstacle geometry')
            edges = np.concatenate((triangles[:, [0,1]], triangles[:, [1,2]], triangles[:, [2,0]]))
            edges = edges.astype(np.uint64)
            packed = (np.minimum(edges[:,0],edges[:,1]) << np.uint64(32)) | np.maximum(edges[:,0],edges[:,1])
            _unique, counts = np.unique(packed, return_counts=True)
            span = vertices.max(axis=0) - vertices.min(axis=0)
            surfaces.append(Surface(BVHTree.FromPolygons(vertices.tolist(), triangles.tolist(), all_triangles=True),
                (vertices.max(axis=0) + vertices.min(axis=0)) * .5,
                max(.01, float(np.linalg.norm(span)) * .25),
                -1. if matrix.determinant() < 0 else 1., bool(np.all(counts == 2)),
                vertices.min(axis=0), vertices.max(axis=0)))
            if len(_surface_cache) >= 64:
                _surface_cache.pop(next(iter(_surface_cache)))
                _sample_cache.clear()
            _surface_cache[surface_key] = surfaces[-1]
    finally:
        _building = False
    if cached:
        if any(existing[:3] != key[:3] for existing in _cache):
            _cache.clear()
        if key not in _cache and len(_cache) >= 3:
            _cache.pop(next(iter(_cache)))
        _cache[key] = tuple(surfaces)
    return MeshSampler(surfaces, cancel)


def preview_points(context, modes, minimum, maximum, *, budget=512):
    """Separate surface-local clusters: no arrows in gaps between objects."""
    mode = 'SCENE' if modes & {'SCENE','NONE'} else 'COLLIDERS'
    surfaces = sampler(context, mode, minimum, maximum, cached=True, include_targets=True).surfaces
    if not surfaces:
        return np.empty((0,3)), np.empty(0)
    resolution = max(3,min(7,int((budget/len(surfaces))**(1/3))))
    points, limits = [], []
    for surface in surfaces:
        span = surface.maximum - surface.minimum
        padding = max(.01, float(span.max()) * .15)
        grid = sample_points(surface.minimum-padding, surface.maximum+padding,
                             (resolution,)*3).reshape(-1,3)
        # Filter bounding-box voids as well: keep a thin band near the mesh.
        band = padding * 1.8
        keep = [point for point in grid
                if (hit := surface.tree.find_nearest(Vector(tuple(point)), band))[0] is not None]
        if keep:
            points.extend(keep)
            length = min(1., max(.01, float((span+2*padding).min())/(resolution-1)*.8))
            limits.extend([length]*len(keep))
    if not points:
        return np.empty((0,3)), np.empty(0)
    points = np.asarray(points)
    _unique, indices = np.unique(points, axis=0, return_index=True)
    indices.sort()
    if len(indices)>budget:
        indices = indices[np.linspace(0,len(indices)-1,budget,dtype=int)]
    return points[indices], np.asarray(limits)[indices]


def settings_signature(context, modes):
    """Property-only obstacle identity, safe for panels and idle timers."""
    if not modes or modes == {'NONE'}:
        return ''
    from . import solver_test
    rows = []
    for obj, matrix, identity, parent in candidates(context,'SCENE' if 'SCENE' in modes else 'COLLIDERS'):
        safe, dependencies, reason = solver_test._safe_object_dependency_identity(obj,collider_capture=True)
        rows.append({'name':obj.name_full,
            'data':str(getattr(getattr(obj,'data',None),'name_full','')),
            'matrix':[list(row) for row in (matrix if matrix is not None else obj.matrix_world)],
            'instance':identity,'animation':solver_test._animation_signature(obj),
            'parent_animation':solver_test._animation_signature(parent) if parent is not None else '',
            'dependencies':dependencies,'safe':safe,'reason':reason})
    return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()


def signature(context, modes):
    """Include non-Collider scene obstacles in attached-cache identity."""
    if not modes or modes == {'NONE'}:
        return ''
    from . import solver_test
    rows = []
    for obj, instance_matrix, identity, parent in candidates(context, 'SCENE' if 'SCENE' in modes else 'COLLIDERS'):
        safe, dependencies, reason = solver_test._safe_object_dependency_identity(obj, collider_capture=True)
        evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            points = np.empty(len(mesh.vertices)*3, dtype='<f4'); mesh.vertices.foreach_get('co', points)
            mesh.calc_loop_triangles()
            triangles = np.empty(len(mesh.loop_triangles)*3, dtype='<i4')
            mesh.loop_triangles.foreach_get('vertices', triangles)
            rows.append({'name':obj.name_full, 'shape':hashlib.sha256(points.tobytes()+triangles.tobytes()).hexdigest(),
                'matrix': [list(row) for row in (instance_matrix if instance_matrix is not None else evaluated.matrix_world)],
                'instance':identity, 'instance_parent_animation':solver_test._animation_signature(parent) if parent is not None else '',
                'animation':solver_test._animation_signature(obj), 'dependencies':dependencies,
                'safe':safe, 'reason':reason})
        finally:
            evaluated.to_mesh_clear()
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
