# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender regression for official frontend batches after Bake failure."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next import intersection_diagnostics as diagnostics
from cloth_next.blender import intersection_overlay, solver_test
from cloth_next.core.errors import ClothNextError, ErrorCategory, ErrorRecord

identity = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
objects = []
for name, role, verts in (
        ('Cloth', 'CLOTH', ((-1, 0, 0), (1, 0, 0), (0, 1, 0))),
        ('Body', 'COLLIDER', ((0, .2, -1), (0, .2, 1), (0, .8, 0)))):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    objects.append((SimpleNamespace(uuid=name, name=name, vertices_local=verts,
                                   triangles=((0, 1, 2),), transform=identity),
                    role, (0,), False))
snapshot = diagnostics.build_solver_input_snapshot(objects, bake_start_frame=1)
raw = {'type': 'self_intersection', 'count': 1,
       'tris': [[item.vertices for item in snapshot.triangles]]}
error = ClothNextError(ErrorRecord.create(
    category=ErrorCategory.SIMULATION, user_message='Intersections detected (1).',
    technical_message='ValidationError: 1 self-intersections (1 tri-tri).',
    recommended_action='Repair the highlighted faces.'), violations=(raw,),
    solver_total_count=1)
result = solver_test._convert_solver_violations(
    SimpleNamespace(solver_input=snapshot), error)
assert result.mapped_count == result.detailed_count == result.detected_count == 1
assert len(result.violations[0].elements) == 2
assert result.violations[0].classification == 'INITIAL_COLLIDER_PENETRATION'
try:
    intersection_overlay.set_diagnostic_session(result)
    assert intersection_overlay._draw_handle is not None
    assert intersection_overlay._label_handle is not None
    assert intersection_overlay.primitives_for_diagnostics(
        intersection_overlay.presentation_diagnostics())
    intersection_overlay.set_diagnostic_session(result)
    assert intersection_overlay.mapped_count() == 1
finally:
    intersection_overlay.clear()
assert intersection_overlay._draw_handle is None
assert intersection_overlay._label_handle is None
print('CLOTH_NEXT_INTERSECTION_BATCH_SMOKE_PASS')
