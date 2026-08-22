from __future__ import annotations

import pytest

from cloth_next.veyra.weld import (
    TopologyTransaction, WeldVertex, conservative_tolerance, plan_safe_welds)


def vertex(index, coordinate, *, island=0, boundary=True, attributes=("a",),
           adjacent=(), faces=()):
    return WeldVertex(index, coordinate, island, boundary, attributes,
                      frozenset(adjacent), frozenset(faces))


def test_exact_and_floating_noise_duplicates_are_planned():
    scale = 1.0
    plan = plan_safe_welds("cloth", (
        vertex(0, (0.0, 0.0, 0.0)),
        vertex(1, (0.0, 0.0, 0.0)),
        vertex(2, (conservative_tolerance(scale) * .5, 0.0, 0.0)),
    ), local_scale=scale)
    assert plan.clusters == ((0, 1, 2),)
    assert plan.merged_vertices == 2


def test_threshold_rejects_nearby_layer():
    plan = plan_safe_welds("cloth", (
        vertex(0, (0.0, 0.0, 0.0)),
        vertex(1, (1.0e-7, 0.0, 0.0)),
    ), local_scale=1.0)
    assert plan.clusters == ()


def test_object_identity_is_mandatory():
    with pytest.raises(ValueError, match="UUID"):
        plan_safe_welds("", (), local_scale=1.0)


def test_disconnected_sheets_fail_closed_without_object_invariant():
    values = (vertex(0, (0.0, 0.0, 0.0), island=0, boundary=False),
              vertex(1, (0.0, 0.0, 0.0), island=1, boundary=False))
    rejected = plan_safe_welds("cloth", values, local_scale=1.0)
    accepted = plan_safe_welds(
        "cloth", values, local_scale=1.0, allow_disconnected_islands=True)
    assert rejected.skip_reasons == {"DISCONNECTED_SHEETS": 1}
    assert accepted.clusters == ((0, 1),)


@pytest.mark.parametrize(("change", "reason"), [
    ({"boundary": False}, "NON_BOUNDARY"),
    ({"attributes": ("different",)}, "POINT_ATTRIBUTE_CONFLICT"),
    ({"adjacent": (0,)}, "EXISTING_EDGE"),
    ({"faces": (3,)}, "SHARED_FACE"),
])
def test_ambiguous_or_destructive_clusters_are_skipped(change, reason):
    left = vertex(0, (0.0, 0.0, 0.0), faces=(3,)
                  if reason == "SHARED_FACE" else ())
    right = vertex(1, (0.0, 0.0, 0.0), **change)
    plan = plan_safe_welds("cloth", (left, right), local_scale=1.0)
    assert plan.clusters == ()
    assert plan.skip_reasons == {reason: 1}


class Mesh:
    def __init__(self, value):
        self.value = value


def transaction():
    installed = []
    disposed = []
    original = Mesh("original")
    working = Mesh("working")
    tx = TopologyTransaction(
        original, working, install=installed.append, dispose=disposed.append,
        digest=lambda mesh: mesh.value)
    return tx, installed, disposed, original, working


def test_topology_transaction_accepts_working_state():
    tx, installed, disposed, original, working = transaction()
    tx.begin()
    tx.accept()
    assert installed == [working]
    assert disposed == [original]


@pytest.mark.parametrize("outcome", ["equal", "increased", "exception", "cancel"])
def test_topology_transaction_rolls_back_all_non_accept_outcomes(outcome):
    tx, installed, disposed, original, working = transaction()
    tx.begin()
    tx.rollback()
    assert outcome
    assert installed == [working, original]
    assert disposed == [working]
    assert not tx.active


def test_topology_transaction_verifies_original_structure():
    tx, _installed, _disposed, original, _working = transaction()
    tx.begin()
    original.value = "corrupt"
    with pytest.raises(RuntimeError, match="verification"):
        tx.rollback()
