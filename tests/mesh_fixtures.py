# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Instrumented synthetic Blender meshes for UI-performance tests.

Mirrors the cost model of the real ``bpy`` data API closely enough to make
draw-path regressions measurable:

* iterating ``mesh.vertices`` / ``edges`` / ``polygons`` materializes one
  Python proxy per element, exactly like ``bpy_prop_collection`` does, so a
  full scan really is O(n) Python work;
* ``vertex.groups`` yields per-membership proxies, which is what makes the
  classic pin scan expensive;
* ``foreach_get`` fills a preallocated buffer straight from a backing NumPy
  array, which is why it is the cheap path in Blender too.

Every scan bumps a counter on :class:`MeshCounters`, so a test can assert
"100 redraws performed zero full mesh scans" instead of trusting a timing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace

import numpy


@dataclass
class MeshCounters:
    """Counts the operations that must never happen in a Panel.draw()."""

    vertex_scans: int = 0
    edge_scans: int = 0
    polygon_scans: int = 0
    loop_scans: int = 0
    vertex_group_scans: int = 0
    foreach_get_calls: int = 0
    to_mesh_calls: int = 0
    topology_hashes: int = 0
    pin_scans: int = 0
    settings_fingerprints: int = 0

    @property
    def full_mesh_scans(self) -> int:
        return (self.vertex_scans + self.edge_scans + self.polygon_scans
                + self.loop_scans + self.vertex_group_scans)

    def reset(self) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, 0)

    def snapshot(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


class _Vector(tuple):
    """Minimal mathutils.Vector stand-in with .x/.y/.z."""

    __slots__ = ()

    @property
    def x(self):
        return self[0]

    @property
    def y(self):
        return self[1]

    @property
    def z(self):
        return self[2]


class _GroupElement:
    __slots__ = ("group", "weight")

    def __init__(self, group, weight):
        self.group = group
        self.weight = weight


class _Vertex:
    __slots__ = ("index", "co", "_mesh")

    def __init__(self, index, co, mesh):
        self.index = index
        self.co = co
        self._mesh = mesh

    @property
    def groups(self):
        # Blender builds this collection per access; the pin scan pays for it.
        self._mesh.counters.vertex_group_scans += 1
        weight = self._mesh.group_weights.get(self.index)
        if weight is None:
            return ()
        return (_GroupElement(self._mesh.group_index, weight),)


class _Edge:
    __slots__ = ("index", "vertices")

    def __init__(self, index, vertices):
        self.index = index
        self.vertices = vertices


class _Polygon:
    __slots__ = ("index", "vertices", "loop_start", "loop_total")

    def __init__(self, index, vertices, loop_start, loop_total):
        self.index = index
        self.vertices = vertices
        self.loop_start = loop_start
        self.loop_total = loop_total


class _Loop:
    __slots__ = ("index", "vertex_index")

    def __init__(self, index, vertex_index):
        self.index = index
        self.vertex_index = vertex_index


class _LoopTriangle:
    __slots__ = ("vertices",)

    def __init__(self, vertices):
        self.vertices = vertices


class _Collection:
    """A lazy bpy_prop_collection: proxies are built on iteration/index."""

    def __init__(self, mesh, count, factory, counter):
        self._mesh = mesh
        self._count = count
        self._factory = factory
        self._counter = counter

    def __len__(self):
        return self._count

    def __iter__(self):
        counters = self._mesh.counters
        setattr(counters, self._counter,
                getattr(counters, self._counter) + 1)
        factory = self._factory
        for index in range(self._count):
            yield factory(index)

    def __getitem__(self, index):
        return self._factory(index)

    def foreach_get(self, attribute, buffer):
        self._mesh.counters.foreach_get_calls += 1
        source = self._mesh.arrays[(self._counter, attribute)]
        buffer[:] = source.reshape(-1)[:len(buffer)]


class SyntheticMesh:
    """A flat n x n grid of quads with the API surface Cloth NeXt reads."""

    def __init__(self, side: int, *, name: str = "Mesh",
                 counters: MeshCounters | None = None,
                 pinned_fraction: float = 0.0):
        self.name = name
        self.counters = counters or MeshCounters()
        self.side = side
        vertex_count = side * side
        quad_count = (side - 1) * (side - 1)

        grid = numpy.arange(vertex_count, dtype=numpy.uint32).reshape(side, side)
        xs, ys = numpy.meshgrid(numpy.arange(side, dtype=numpy.float64),
                                numpy.arange(side, dtype=numpy.float64),
                                indexing="ij")
        self._co = numpy.stack(
            [xs.ravel(), ys.ravel(), numpy.zeros(vertex_count)], axis=1)

        horizontal = numpy.stack(
            [grid[:, :-1].ravel(), grid[:, 1:].ravel()], axis=1)
        vertical = numpy.stack(
            [grid[:-1, :].ravel(), grid[1:, :].ravel()], axis=1)
        edges = numpy.concatenate([horizontal, vertical], axis=0)

        corners = numpy.stack([grid[:-1, :-1].ravel(), grid[1:, :-1].ravel(),
                               grid[1:, 1:].ravel(), grid[:-1, 1:].ravel()],
                              axis=1).astype(numpy.uint32)

        self._edges = edges.astype(numpy.uint32)
        self._polys = corners
        self._loop_vertices = corners.reshape(-1)
        self._loop_start = (numpy.arange(quad_count, dtype=numpy.uint32) * 4)
        self._loop_total = numpy.full(quad_count, 4, dtype=numpy.uint32)

        # Two triangles per quad, matching Blender's fan triangulation order.
        self._triangles = numpy.concatenate(
            [corners[:, (0, 1, 2)], corners[:, (0, 2, 3)]], axis=0)

        self.arrays = {
            ("vertex_scans", "co"): self._co.astype(numpy.float32),
            ("edge_scans", "vertices"): self._edges,
            ("polygon_scans", "loop_start"): self._loop_start,
            ("polygon_scans", "loop_total"): self._loop_total,
            ("loop_scans", "vertex_index"): self._loop_vertices,
        }

        self.group_index = 0
        self.group_weights: dict[int, float] = {}
        if pinned_fraction > 0.0:
            pinned = int(vertex_count * pinned_fraction)
            for index in range(pinned):
                self.group_weights[index] = 1.0

        self.vertices = _Collection(
            self, vertex_count,
            lambda i: _Vertex(i, _Vector(self._co[i]), self), "vertex_scans")
        self.edges = _Collection(
            self, len(self._edges),
            lambda i: _Edge(i, tuple(int(v) for v in self._edges[i])),
            "edge_scans")
        self.polygons = _Collection(
            self, quad_count,
            lambda i: _Polygon(i, tuple(int(v) for v in self._polys[i]),
                               int(self._loop_start[i]),
                               int(self._loop_total[i])), "polygon_scans")
        self.loops = _Collection(
            self, len(self._loop_vertices),
            lambda i: _Loop(i, int(self._loop_vertices[i])), "loop_scans")
        self.loop_triangles = _Collection(
            self, len(self._triangles),
            lambda i: _LoopTriangle(tuple(int(v) for v in self._triangles[i])),
            "polygon_scans")

    def calc_loop_triangles(self):
        return None

    # -- mutations used by the topology-hash tests ---------------------------

    def move_vertex(self, index: int, offset: float) -> None:
        """Position-only change: must not alter the topology hash."""
        self._co[index] += offset
        self.arrays[("vertex_scans", "co")] = self._co.astype(numpy.float32)

    def drop_edge(self, index: int) -> None:
        self._edges = numpy.delete(self._edges, index, axis=0)
        self.arrays[("edge_scans", "vertices")] = self._edges
        self.edges = _Collection(
            self, len(self._edges),
            lambda i: _Edge(i, tuple(int(v) for v in self._edges[i])),
            "edge_scans")

    def swap_loop_vertices(self, first: int, second: int) -> None:
        self._loop_vertices = self._loop_vertices.copy()
        self._loop_vertices[[first, second]] = \
            self._loop_vertices[[second, first]]
        self.arrays[("loop_scans", "vertex_index")] = self._loop_vertices


class VertexGroup:
    __slots__ = ("name", "index")

    def __init__(self, name, index):
        self.name = name
        self.index = index


class VertexGroups:
    """Name lookup only — never a membership scan."""

    def __init__(self, names=()):
        self._groups = {name: VertexGroup(name, index)
                        for index, name in enumerate(names)}

    def get(self, name, default=None):
        return self._groups.get(name, default)

    def __contains__(self, name):
        return name in self._groups

    def __iter__(self):
        return iter(self._groups.values())

    def __len__(self):
        return len(self._groups)

    def remove_group(self, name):
        self._groups.pop(name, None)


def grid_side_for(vertex_count: int) -> int:
    """Grid side length whose square is closest to ``vertex_count``."""
    return max(2, int(math.sqrt(vertex_count)))


def build_mesh(vertex_count: int, *, counters: MeshCounters | None = None,
               pinned_fraction: float = 0.0, name: str = "Mesh") -> SyntheticMesh:
    return SyntheticMesh(grid_side_for(vertex_count), name=name,
                         counters=counters, pinned_fraction=pinned_fraction)


PIN_GROUP = "Pins"


@dataclass
class Scene:
    cloth: object
    collider: object
    context: object
    counters: MeshCounters
    bpy: object = None


class _Layout:
    """Records what a panel drew; ignores everything else."""

    def __init__(self, sink=None):
        self.sink = sink or self
        if sink is None:
            self.labels: list[str] = []
            self.operators: list[tuple] = []
            self.props: list[tuple] = []
        self.enabled = True
        self.scale_y = 1.0
        self.use_property_split = False
        self.use_property_decorate = False
        self.alert = False

    def label(self, text="", **_kw):
        self.sink.labels.append(text)

    def operator(self, identifier, text="", **_kw):
        self.sink.operators.append((identifier, text, self.enabled))
        return _Operator()

    def prop(self, _data, name="", **_kw):
        self.sink.props.append(name)

    def prop_search(self, _data, name="", *_a, **_kw):
        self.sink.props.append(name)

    def menu(self, *_a, **_kw):
        return None

    def separator(self, *_a, **_kw):
        return None

    def row(self, **_kw):
        return _Layout(self.sink)

    def split(self, **_kw):
        return _Layout(self.sink)

    def column(self, **_kw):
        return _Layout(self.sink)

    def box(self, **_kw):
        return _Layout(self.sink)


class _Operator:
    def __init__(self):
        self.role = ""
        self.tooltip = ""


def draw_panel(panel_cls, context):
    """Instantiate a real panel class and run its production draw()."""
    panel = panel_cls()
    panel.layout = _Layout()
    panel.draw(context)
    return panel.layout


def build_cloth_scene(bpy, *, vertex_count: int = 64,
                      counters: MeshCounters | None = None,
                      pinning: bool = False, pinned_fraction: float = 0.25,
                      pin_group: str = PIN_GROUP,
                      enabled: bool = True) -> Scene:
    """A one-cloth/one-collider scene backed by instrumented synthetic meshes."""
    counters = counters or MeshCounters()

    cloth = bpy.types.Object(name="Cloth", type="MESH")
    cloth.data = build_mesh(vertex_count, counters=counters, name="ClothMesh",
                            pinned_fraction=pinned_fraction if pinning else 0.0)
    cloth.animation_data = None
    cloth.constraints = ()
    cloth.matrix_world = tuple(tuple(1.0 if r == c else 0.0 for c in range(4))
                               for r in range(4))
    cloth.vertex_groups = VertexGroups((pin_group,) if pinning else ())
    cloth.cloth_next.enabled = enabled
    cloth.cloth_next.role = "CLOTH"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 24
    if pinning:
        cloth.cloth_next.pinning_enabled = True
        cloth.cloth_next.pin_group = pin_group

    collider = bpy.types.Object(name="Collider", type="MESH")
    collider.data = build_mesh(64, counters=counters, name="ColliderMesh")
    collider.animation_data = None
    collider.constraints = ()
    collider.matrix_world = cloth.matrix_world
    collider.vertex_groups = VertexGroups()
    collider.cloth_next.enabled = enabled
    collider.cloth_next.role = "COLLIDER"

    prefs = SimpleNamespace(auto_launch_bake_window=True,
                            telemetry_refresh_seconds=1.0,
                            external_solver_path="", developer_tools=False)
    scene = SimpleNamespace(objects=[cloth, collider], frame_start=1,
                            frame_end=24, frame_current=1,
                            render=SimpleNamespace(fps=24),
                            gravity=(0.0, 0.0, -9.81), use_gravity=True,
                            cloth_next_quality=None)

    def frame_set(frame, subframe=0.0):
        scene.frame_current = int(frame)

    scene.frame_set = frame_set
    context = SimpleNamespace(
        object=cloth, active_object=cloth, scene=scene,
        view_layer=SimpleNamespace(update=lambda: None),
        evaluated_depsgraph_get=lambda: SimpleNamespace(),
        preferences=SimpleNamespace(
            addons={"cloth_next": SimpleNamespace(preferences=prefs)}))
    return Scene(cloth=cloth, collider=collider, context=context,
                 counters=counters, bpy=bpy)


def attach_cache(cloth, *, settings_fingerprint: str,
                 geometry_fingerprint: str = "", version: int = 0):
    """Give the cloth a Cloth NeXt-owned playback modifier and a baked result."""
    modifier = cloth.modifiers.new("Cloth NeXt Test Cache", "MESH_CACHE")
    modifier.filepath = "/fake/cache/cn_test_cloth_x.pc2"
    modifier.cloth_next_owner = "cloth_next_playback_v1"
    cloth.cloth_next_cache_path = modifier.filepath
    cloth.cloth_next.baked_settings_fingerprint = settings_fingerprint
    cloth.cloth_next.baked_geometry_fingerprint = geometry_fingerprint
    cloth.cloth_next.baked_fingerprint_version = version
    return modifier
