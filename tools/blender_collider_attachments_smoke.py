"""Real Blender/export/CUDA regression for static and animated Collider targets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cloth_next
from cloth_next import export_identity
from cloth_next.attachments import AttachmentError
from cloth_next.blender import object_attachments, solver_test
from cloth_next.ppf.coordinates import blender_position_to_ppf
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.schema import envelope
from cloth_next.ppf_run.session import SolverSession


def mesh(name, role, z):
    data = bpy.data.meshes.new(name)
    if role == "SOFT_BODY":
        data.from_pydata([(0, 0, z), (.12, 0, z), (.12, .12, z), (0, .12, z),
                          (0, 0, z + .06), (.12, 0, z + .06),
                          (.12, .12, z + .06), (0, .12, z + .06)], [],
                         [(0, 3, 2), (0, 2, 1), (4, 5, 6), (4, 6, 7),
                          (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
                          (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)])
    else:
        data.from_pydata([(0, 0, z), (.12, 0, z), (0, .12, z)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.clothnext.add_physics()
    bpy.ops.clothnext.set_object_type(role=role)
    group = obj.vertex_groups.new(name="Attach")
    group.add([0], 1.0, "REPLACE")
    return obj


def run_case(kind, resolved, output, source_role="CLOTH"):
    scene = bpy.context.scene
    scene.cloth_next_object_attachments.clear()
    for obj in tuple(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    source = mesh("Attached Source", source_role, .015)
    collider = mesh("Attachment Collider", "COLLIDER", 0)
    source.cloth_next.bake_start = 1
    source.cloth_next.bake_end = 9
    scene.gravity = (0, 0, 0)
    shape = None
    if kind != "STATIC":
        collider.cloth_next.collider_motion = "ANIMATED"
        if kind == "RIGID_ANIMATED":
            collider.keyframe_insert("location", frame=1)
            collider.location.z = .08
            collider.keyframe_insert("location", frame=9)
        else:
            collider.shape_key_add(name="Basis")
            shape = collider.shape_key_add(name="Deform")
            shape.data[0].co.z = .08
            shape.value = 0
            shape.keyframe_insert("value", frame=1)
            shape.value = 1
            shape.keyframe_insert("value", frame=9)
    scene.frame_set(1)
    bpy.context.view_layer.objects.active = source
    assert bpy.ops.clothnext.add_group_attachment() == {"FINISHED"}
    item = scene.cloth_next_object_attachments[-1]
    item.target_object = collider
    item.source_group = item.target_group = "Attach"
    assert bpy.ops.clothnext.bind_group_attachment(index=0) == {"FINISHED"}
    item.stiffness = 5000
    original_binding = tuple(item.points[0].target_triangle), tuple(item.points[0].target_weights)
    if shape:
        scene.frame_set(9)
        authored_world = object_attachments._mesh_triangles(collider)[0][0]
        assert abs(authored_world[2] - .08) < 1e-6
        scene.frame_set(1)
    # Snapshot must use frame 1 even when the user's timeline is elsewhere.
    scene.frame_set(7, subframe=.25)
    snapshot = solver_test.validate_scene(bpy.context)
    assert scene.frame_current == 7 and abs(scene.frame_subframe - .25) < 1e-6
    relation = snapshot.object_attachments[0][0]
    assert relation.target_role == "COLLIDER"
    np.testing.assert_allclose(relation.points[0].target_point, (0, 0, 0), atol=1e-6)
    plan = solver_test.build_run_plan(bpy.context, snapshot=snapshot)
    params = envelope.loads_envelope(plan.scene.param_payload, envelope.KIND_PARAM, schema_version=2)
    record = params["cross_stitch"][0]
    assert record["target_uuid"] == export_identity.export_uuid(collider)
    assert record["stitch_stiffness"] == 5000
    assert params["pin_config"] == {}
    raw = plan.scene.data_payload
    data = envelope.loads_envelope(raw.read_bytes() if isinstance(raw, Path) else raw,
                                  envelope.KIND_SCENE, schema_version=2)
    target_wire = next(obj for group in data for obj in group["object"]
                       if obj["uuid"] == record["target_uuid"])
    if kind == "RIGID_ANIMATED":
        assert "transform_animation" in target_wire
    elif kind == "DEFORMING_ANIMATED":
        assert "static_deform_animation" in target_wire
    frames = []
    SolverSession(resolved=resolved, scene=plan.scene,
                  work_directory=output / kind, frame_sink=frames.append).run()
    assert len(frames) == 8
    uuid = export_identity.export_uuid(source)
    final = np.asarray(frames[-1].positions_by_uuid[uuid])
    assert np.isfinite(final).all()
    scene.frame_set(9)
    target_world = object_attachments._mesh_triangles(collider)[0][0]
    expected = np.asarray(blender_position_to_ppf(target_world))
    gap = float(np.linalg.norm(final[0] - expected))
    assert gap < .025, (kind, gap, final[0], expected)
    if kind != "STATIC":
        assert final[0][1] > .04, (kind, final[0])
    assert original_binding == (tuple(item.points[0].target_triangle), tuple(item.points[0].target_weights))
    item.enabled = False
    assert not solver_test.validate_scene(bpy.context).object_attachments
    item.enabled = True
    collider.cloth_next.enabled = False
    try:
        solver_test.validate_scene(bpy.context)
        raise AssertionError("Disabled collider was accepted")
    except (AttachmentError, solver_test.SceneValidationError):
        pass
    return {"kind": kind, "source_role": source_role, "frames": len(frames), "final_anchor_gap_m": gap,
            "source_final": final[0].tolist(), "target_final": expected.tolist()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-role", choices=("CLOTH", "SOFT_BODY"), default="CLOTH")
    parser.add_argument("--motion", choices=("ALL", "STATIC", "RIGID_ANIMATED", "DEFORMING_ANIMATED"), default="ALL")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    args.output = args.output.resolve()
    resolved = SolverResolver(solver_test._version_probe).resolve(
        SolverResolutionContext(development_executable=args.solver.resolve()))
    assert resolved and resolved.protocol_version == "0.23"
    cloth_next.register()
    original_resolve = solver_test.resolve_solver
    solver_test.resolve_solver = lambda _context: resolved
    try:
        args.output.mkdir(parents=True, exist_ok=True)
        kinds = (("STATIC", "RIGID_ANIMATED", "DEFORMING_ANIMATED")
                 if args.motion == "ALL" else (args.motion,))
        report = {"result": "PASS", "cases": [run_case(kind, resolved, args.output, args.source_role)
            for kind in kinds]}
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    finally:
        solver_test.resolve_solver = original_resolve
        cloth_next.unregister()


if __name__ == "__main__":
    main()
