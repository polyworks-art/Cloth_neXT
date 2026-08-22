# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure global near-duplicate welding on a real Blender mesh copy."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from math import dist
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils.kdtree import KDTree


values = sys.argv[sys.argv.index("--") + 1:]
parser = argparse.ArgumentParser()
parser.add_argument("--object", default="Shorts")
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--threshold", action="append", type=float, required=True)
args = parser.parse_args(values)


def point(value):
    return tuple(float(component) for component in value)


def mesh_counts(mesh):
    mesh.calc_loop_triangles()
    return {
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "polygons": len(mesh.polygons),
        "triangles": len(mesh.loop_triangles),
    }


def triangle_area(a, b, c):
    ab = tuple(b[i] - a[i] for i in range(3))
    ac = tuple(c[i] - a[i] for i in range(3))
    cross = (ab[1] * ac[2] - ab[2] * ac[1],
             ab[2] * ac[0] - ab[0] * ac[2],
             ab[0] * ac[1] - ab[1] * ac[0])
    return .5 * sum(value * value for value in cross) ** .5


def degenerate_count(mesh):
    mesh.calc_loop_triangles()
    return sum(triangle_area(*(mesh.vertices[index].co
                               for index in triangle.vertices))
               <= 1.0e-15 for triangle in mesh.loop_triangles)


def zero_length_edges(mesh):
    return sum(dist(point(mesh.vertices[edge.vertices[0]].co),
                    point(mesh.vertices[edge.vertices[1]].co))
               <= 1.0e-15 for edge in mesh.edges)


def duplicate_faces(mesh):
    seen = Counter(tuple(sorted(vertex for vertex in polygon.vertices))
                   for polygon in mesh.polygons)
    return sum(count - 1 for count in seen.values() if count > 1)


def non_manifold_edges(mesh):
    uses = Counter()
    for polygon in mesh.polygons:
        vertices = tuple(polygon.vertices)
        for offset, left in enumerate(vertices):
            right = vertices[(offset + 1) % len(vertices)]
            uses[tuple(sorted((int(left), int(right))))] += 1
    return sum(count != 2 for count in uses.values())


def topology_sha(mesh):
    payload = {
        "vertices": [point(vertex.co) for vertex in mesh.vertices],
        "edges": [tuple(map(int, edge.vertices)) for edge in mesh.edges],
        "faces": [(tuple(map(int, polygon.vertices)), int(polygon.material_index))
                  for polygon in mesh.polygons],
    }
    return hashlib.sha256(json.dumps(
        payload, separators=(",", ":")).encode("utf-8")).hexdigest()


class UnionFind:
    def __init__(self, count):
        self.parent = list(range(count))

    def find(self, value):
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left, right):
        left = self.find(left)
        right = self.find(right)
        if left != right:
            self.parent[max(left, right)] = min(left, right)


def mesh_islands(mesh):
    neighbors = defaultdict(set)
    for edge in mesh.edges:
        left, right = map(int, edge.vertices)
        neighbors[left].add(right)
        neighbors[right].add(left)
    unseen = set(range(len(mesh.vertices)))
    island_by_vertex = {}
    sizes = []
    while unseen:
        first = min(unseen)
        unseen.remove(first)
        queue = deque((first,))
        members = []
        while queue:
            current = queue.popleft()
            members.append(current)
            for neighbor in sorted(neighbors[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        island_id = len(sizes)
        sizes.append(len(members))
        for vertex in members:
            island_by_vertex[vertex] = island_id
    return island_by_vertex, sizes


def vertex_signatures(obj):
    mesh = obj.data
    faces = defaultdict(set)
    edges = defaultdict(set)
    for polygon in mesh.polygons:
        for vertex in polygon.vertices:
            faces[int(vertex)].add((int(polygon.material_index), int(polygon.index)))
    sharp_attribute = mesh.attributes.get("sharp_edge")
    for edge in mesh.edges:
        sharp = bool(sharp_attribute.data[edge.index].value) if sharp_attribute else False
        signature = (bool(edge.use_seam), sharp)
        for vertex in edge.vertices:
            edges[int(vertex)].add(signature)
    uv = defaultdict(lambda: defaultdict(set))
    for layer in mesh.uv_layers:
        for polygon in mesh.polygons:
            for loop_index in polygon.loop_indices:
                loop = mesh.loops[loop_index]
                uv[int(loop.vertex_index)][layer.name].add(
                    tuple(round(float(value), 12)
                          for value in layer.data[loop_index].uv))
    groups = defaultdict(dict)
    for vertex in mesh.vertices:
        for membership in vertex.groups:
            groups[int(vertex.index)][int(membership.group)] = round(
                float(membership.weight), 12)
    return {
        index: {
            "materials": sorted({material for material, _face in faces[index]}),
            "faces": sorted(face for _material, face in faces[index]),
            "edge_flags": sorted(edges[index]),
            "uv": {name: sorted(values) for name, values in sorted(uv[index].items())},
            "groups": groups[index],
        }
        for index in range(len(mesh.vertices))}


def clusters_for(obj, threshold, islands, signatures):
    mesh = obj.data
    coordinates = [point(obj.matrix_world @ vertex.co) for vertex in mesh.vertices]
    tree = KDTree(len(coordinates))
    for index, coordinate in enumerate(coordinates):
        tree.insert(coordinate, index)
    tree.balance()
    union = UnionFind(len(coordinates))
    for index, coordinate in enumerate(coordinates):
        for _position, other, distance in tree.find_range(coordinate, threshold):
            if other > index and distance <= threshold:
                union.union(index, other)
    members = defaultdict(list)
    for index in range(len(coordinates)):
        members[union.find(index)].append(index)
    edge_keys = {tuple(sorted(map(int, edge.vertices))) for edge in mesh.edges}
    face_sets = {index: set(signatures[index]["faces"]) for index in signatures}
    result = []
    for values in sorted((tuple(group) for group in members.values()
                          if len(group) > 1), key=lambda item: (item[0], item)):
        distances = [dist(coordinates[left], coordinates[right])
                     for offset, left in enumerate(values)
                     for right in values[offset + 1:]]
        island_ids = {islands[index] for index in values}
        same_uv = len({json.dumps(signatures[index]["uv"], sort_keys=True)
                       for index in values}) == 1
        same_material = len({tuple(signatures[index]["materials"])
                             for index in values}) == 1
        same_flags = len({tuple(signatures[index]["edge_flags"])
                          for index in values}) == 1
        same_groups = len({tuple(sorted(signatures[index]["groups"].items()))
                           for index in values}) == 1
        shares_edge = any(tuple(sorted((left, right))) in edge_keys
                          for offset, left in enumerate(values)
                          for right in values[offset + 1:])
        adjacent_faces = any(face_sets[left].intersection(face_sets[right])
                             for offset, left in enumerate(values)
                             for right in values[offset + 1:])
        exact = max(distances, default=0.0) <= 1.0e-12
        if len(island_ids) == 1 and (shares_edge or adjacent_faces):
            category = "same_connected_fan"
        elif len(island_ids) == 1:
            category = "same_island_unwelded_boundary"
        elif same_uv and same_material and same_flags and same_groups:
            category = "compatible_disconnected_islands"
        else:
            category = "attribute_conflict_or_layered_islands"
        result.append({
            "vertices": values, "size": len(values), "exact": exact,
            "max_distance": max(distances, default=0.0),
            "mean_distance": (sum(distances) / len(distances)
                              if distances else 0.0),
            "island_ids": sorted(island_ids), "same_island": len(island_ids) == 1,
            "shares_edge": shares_edge, "adjacent_faces": adjacent_faces,
            "uv_compatible": same_uv, "material_compatible": same_material,
            "edge_flags_compatible": same_flags,
            "vertex_groups_compatible": same_groups,
            "category": category,
        })
    return result


def merged_copy(obj, threshold):
    copied = obj.data.copy()
    bm = bmesh.new()
    try:
        bm.from_mesh(copied)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=float(threshold))
        bm.to_mesh(copied)
        copied.update()
        return {
            **mesh_counts(copied),
            "degenerates": degenerate_count(copied),
            "zero_length_edges": zero_length_edges(copied),
            "duplicate_faces": duplicate_faces(copied),
            "non_manifold_edges": non_manifold_edges(copied),
            "topology_sha256": topology_sha(copied),
        }
    finally:
        bm.free()
        bpy.data.meshes.remove(copied)


obj = bpy.data.objects.get(args.object)
if obj is None or obj.type != "MESH":
    raise RuntimeError(f"mesh object {args.object!r} was not found")
mesh = obj.data
islands, island_sizes = mesh_islands(mesh)
signatures = vertex_signatures(obj)
baseline = {
    **mesh_counts(mesh),
    "degenerates": degenerate_count(mesh),
    "zero_length_edges": zero_length_edges(mesh),
    "duplicate_faces": duplicate_faces(mesh),
    "non_manifold_edges": non_manifold_edges(mesh),
    "topology_sha256": topology_sha(mesh),
    "island_count": len(island_sizes),
    "island_sizes": sorted(island_sizes, reverse=True),
    "matrix_world": [list(map(float, row)) for row in obj.matrix_world],
    "scale": point(obj.scale),
}
edge_lengths = [dist(point(obj.matrix_world @ mesh.vertices[edge.vertices[0]].co),
                     point(obj.matrix_world @ mesh.vertices[edge.vertices[1]].co))
                for edge in mesh.edges]
baseline["world_edge_length"] = {
    "minimum": min(edge_lengths),
    "mean": sum(edge_lengths) / len(edge_lengths),
    "maximum": max(edge_lengths),
}
rows = []
for threshold in sorted(set(args.threshold)):
    clusters = clusters_for(obj, threshold, islands, signatures)
    pair_distances = [cluster["mean_distance"] for cluster in clusters]
    merged = merged_copy(obj, threshold)
    rows.append({
        "threshold": threshold,
        "cluster_count": len(clusters),
        "merged_vertices": baseline["vertices"] - merged["vertices"],
        "cluster_size_distribution": dict(sorted(Counter(
            cluster["size"] for cluster in clusters).items())),
        "maximum_weld_distance": max((cluster["max_distance"]
                                      for cluster in clusters), default=0.0),
        "average_cluster_pair_distance": (sum(pair_distances) / len(pair_distances)
                                          if pair_distances else 0.0),
        "exact_cluster_count": sum(cluster["exact"] for cluster in clusters),
        "category_counts": dict(sorted(Counter(
            cluster["category"] for cluster in clusters).items())),
        "compatibility": {
            key: sum(cluster[key] for cluster in clusters)
            for key in ("same_island", "uv_compatible", "material_compatible",
                        "edge_flags_compatible", "vertex_groups_compatible")},
        "clusters": clusters,
        "after": merged,
    })

report = {
    "blend": bpy.data.filepath, "object": obj.name,
    "source_sha256": hashlib.sha256(Path(bpy.data.filepath).read_bytes()).hexdigest(),
    "baseline": baseline, "thresholds": rows,
}
args.report.parent.mkdir(parents=True, exist_ok=True)
args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
print("VEYRA_WELD_ANALYSIS_PASS", args.report, flush=True)
