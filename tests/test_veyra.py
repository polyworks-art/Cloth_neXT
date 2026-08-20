from __future__ import annotations

import inspect

import pytest

from cloth_next.veyra.artifacts import SessionArtifacts
from cloth_next.veyra.model import (CompanionMode, RepairArtifact,
                                    VeyraRepairPlan, VeyraStep)
from cloth_next.veyra.solver import VeyraCancelled, solve_repair_plan


def input_value(*, faces=(), pairs=(), validation=()):
    return {
        "schema": "cnx.veyra.input.v1", "job_id": "job",
        "source_snapshot_identity": "a" * 64,
        "desired_separation": 0.01, "pairs": list(pairs),
        "degenerate_faces": list(faces),
        "validation_triangles": list(validation),
    }


def face(vertices=((0, 0, 0), (1, 0, 0), (2, 0, 0)), indices=(0, 1, 2)):
    return {"object_uuid": "cloth", "vertex_indices": indices,
            "vertices": vertices, "source_polygon_index": 4}


def test_position_repair_returns_typed_immutable_plan():
    value = input_value(faces=(face(),), validation=({
        "keys": (("cloth", 0), ("cloth", 1), ("cloth", 2)),
        "vertices": face()["vertices"],
    },))
    plan = solve_repair_plan(value)
    assert isinstance(plan, VeyraRepairPlan)
    assert plan.planned_count == 1 and plan.skipped_count == 0
    assert len(plan.displacements) == 1 and not plan.welds


def test_duplicate_vertex_degenerate_becomes_explicit_id_weld_only():
    duplicate = face(vertices=((0, 0, 0), (1, 0, 0), (0, 0, 0)))
    value = input_value(faces=(duplicate,), validation=({
        "keys": (("cloth", 0), ("cloth", 1), ("cloth", 2)),
        "vertices": duplicate["vertices"],
    },))
    plan = solve_repair_plan(value)
    assert not plan.displacements
    assert plan.welds[0].vertex_indices == (0, 2)
    assert plan.welds[0].source_polygon_indices == (4,)


def test_transitively_joined_weld_group_counts_each_diagnosed_face():
    first = face(
        vertices=((0, 0, 0), (0, 0, 0), (1, 0, 0)),
        indices=(0, 1, 10))
    second = face(
        vertices=((0, 0, 0), (0, 0, 0), (0, 1, 0)),
        indices=(1, 2, 11))
    second["source_polygon_index"] = 5

    plan = solve_repair_plan(input_value(faces=(first, second)))

    assert len(plan.welds) == 1
    assert plan.welds[0].vertex_indices == (0, 1, 2)
    assert plan.planned_count == 2
    assert plan.skipped_count == 0


def test_partial_success_keeps_unsafe_candidate_skipped():
    repairable = face()
    repeated_id = face(indices=(3, 3, 4))
    value = input_value(faces=(repairable, repeated_id), validation=({
        "keys": (("cloth", 0), ("cloth", 1), ("cloth", 2)),
        "vertices": repairable["vertices"],
    },))
    plan = solve_repair_plan(value)
    assert (plan.attempted_count, plan.planned_count, plan.skipped_count) == (2, 1, 1)


def test_cancellation_and_progress_are_bounded_and_monotonic():
    faces = tuple(face(indices=(index * 3, index * 3 + 1, index * 3 + 2))
                  for index in range(20))
    progress = []
    checks = [0]
    def cancelled():
        checks[0] += 1
        return checks[0] > 8
    with pytest.raises(VeyraCancelled):
        solve_repair_plan(input_value(faces=faces), progress=lambda *row:
                          progress.append(row), cancelled=cancelled)
    for step in VeyraStep:
        rows = [row for row in progress if row[0] is step and row[2]]
        assert all(0 <= row[1] <= row[2] for row in rows)
        assert [row[1] for row in rows] == sorted(row[1] for row in rows)


def test_completed_measurable_steps_report_one_hundred_percent():
    rows = []
    solve_repair_plan(input_value(faces=(face(),)),
                      progress=lambda *row: rows.append(row))
    for step in (VeyraStep.ANALYZING_DIAGNOSTICS,
                 VeyraStep.SOLVING_REPAIR_PLAN):
        current, total = [(row[1], row[2]) for row in rows
                          if row[0] is step][-1]
        assert total and current == total


def test_veyra_solver_has_no_bpy_or_ui_import():
    import cloth_next.veyra.solver as solver
    source = inspect.getsource(solver)
    assert "import bpy" not in source
    assert "tkinter" not in source and "companion.app" not in source


def test_artifact_round_trip_digest_job_schema_and_containment(tmp_path):
    store = SessionArtifacts(tmp_path / "session")
    artifact = store.write_json(schema="cnx.veyra.input.v1", job_id="job",
                                name="job.input.json", value={"safe": True})
    assert store.read_json(artifact, schema="cnx.veyra.input.v1",
                           job_id="job") == {"safe": True}
    with pytest.raises(ValueError, match="stale"):
        store.read_json(artifact, schema="cnx.veyra.input.v1", job_id="stale")
    bad = RepairArtifact(artifact.schema, artifact.job_id,
                         artifact.relative_path, artifact.size, "0" * 64)
    with pytest.raises(ValueError, match="digest"):
        store.read_json(bad, schema=artifact.schema, job_id="job")
    outside = RepairArtifact(artifact.schema, "job", "../outside.json", 0,
                             "0" * 64)
    with pytest.raises(ValueError, match="name|escapes"):
        store.read_json(outside, schema=artifact.schema, job_id="job")


def test_companion_modes_are_explicit_not_title_derived():
    assert CompanionMode.BAKE.value == "BAKE"
    assert CompanionMode.VEYRA.value == "VEYRA"
