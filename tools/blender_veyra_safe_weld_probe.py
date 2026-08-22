# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only-source probe for the production VEYRA explicit weld plan."""
import bmesh
import bpy
import json
import math
from pathlib import Path
import sys

repo = Path(sys.argv[sys.argv.index("--") + 1])
report_path = Path(sys.argv[sys.argv.index("--") + 2])
sys.path.insert(0, str(repo))
from cloth_next.blender import solver_test  # noqa: E402
from cloth_next.veyra.weld import WeldVertex, plan_safe_welds  # noqa: E402

obj = bpy.data.objects["Shorts"]
mesh = obj.data
before_hash = solver_test._hash_mesh_topology(mesh)
islands, boundary, neighbors = solver_test._veyra_mesh_islands(mesh)
faces = {index: set() for index in range(len(mesh.vertices))}
for face in mesh.polygons:
    for index in face.vertices:
        faces[int(index)].add(int(face.index))
lengths = [math.dist(tuple(mesh.vertices[e.vertices[0]].co),
                     tuple(mesh.vertices[e.vertices[1]].co)) for e in mesh.edges]
scale = sum(lengths) / len(lengths)
vertices = tuple(WeldVertex(
    int(v.index), tuple(map(float, v.co)), islands[int(v.index)],
    int(v.index) in boundary,
    solver_test._veyra_point_signature(obj, int(v.index)),
    frozenset(neighbors[int(v.index)]), frozenset(faces[int(v.index)]))
    for v in mesh.vertices)
plan = plan_safe_welds("probe", vertices, local_scale=scale,
                       allow_disconnected_islands=True)
before_faces = solver_test._veyra_face_semantics(mesh)
copy = mesh.copy()
bm = bmesh.new()
try:
    bm.from_mesh(copy)
    bm.verts.ensure_lookup_table()
    before_nm = sum(not edge.is_manifold for edge in bm.edges)
    targetmap = {}
    for cluster in plan.clusters:
        target = bm.verts[cluster[0]]
        for index in cluster[1:]:
            targetmap[bm.verts[index]] = target
    bmesh.ops.weld_verts(bm, targetmap=targetmap)
    bm.to_mesh(copy)
    copy.update()
    after_faces = solver_test._veyra_face_semantics(copy)
    removed_semantics = [key for key, count in before_faces.items()
                         if count-after_faces.get(key, 0) > 0 and count < 2]
    removed_polygons = []
    if removed_semantics:
        wanted = set(removed_semantics)
        for polygon, key in zip(mesh.polygons,
                                solver_test._veyra_face_semantics_rows(mesh)):
            if key in wanted:
                removed_polygons.append({"polygon": int(polygon.index),
                    "vertices": tuple(map(int, polygon.vertices)),
                    "material": int(polygon.material_index),
                    "coordinate_matches": [int(other.index) for other in mesh.polygons
                        if other.index != polygon.index and
                        sorted(tuple(round(float(x), 12) for x in mesh.vertices[i].co)
                               for i in other.vertices) ==
                        sorted(tuple(round(float(x), 12) for x in mesh.vertices[i].co)
                               for i in polygon.vertices)]})
    row = {
        "clusters": len(plan.clusters), "merged_vertices": plan.merged_vertices,
        "skipped": plan.skip_reasons, "tolerance": plan.tolerance,
        "before_non_manifold": before_nm,
        "after_non_manifold": sum(not edge.is_manifold for edge in bm.edges),
        "after_degenerates": sum(face.calc_area() <= 1e-12 for face in bm.faces),
        "after_zero_edges": sum(edge.calc_length() <= 1e-12 for edge in bm.edges),
        "after_vertices": len(copy.vertices), "after_edges": len(copy.edges),
        "after_polygons": len(copy.polygons),
        "new_face_semantics": sum(max(0, count-before_faces.get(key, 0))
                                  for key, count in after_faces.items()),
        "unique_face_semantics_removed": sum(
            count-after_faces.get(key, 0) for key, count in before_faces.items()
            if count-after_faces.get(key, 0) > 0 and count < 2),
        "removed_polygons": removed_polygons,
        "original_unchanged": before_hash == solver_test._hash_mesh_topology(mesh),
        "cluster_vertices": plan.clusters,
    }
finally:
    bm.free()
    bpy.data.meshes.remove(copy)
report_path.write_text(json.dumps(row, indent=2), encoding="utf-8")
print("VEYRA_SAFE_WELD_PROBE", json.dumps(row), flush=True)
