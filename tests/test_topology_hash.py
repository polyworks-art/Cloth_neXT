# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The topology signature must identify connectivity — and only connectivity."""

from __future__ import annotations

import json
import tracemalloc

import pytest

from cloth_next import topology
from tests import mesh_fixtures


@pytest.fixture
def mesh():
    return mesh_fixtures.build_mesh(2_500)


def test_identical_topology_is_deterministic(mesh):
    first = topology.mesh_topology_signature(mesh)
    second = topology.mesh_topology_signature(mesh)
    assert first == second
    # A freshly built, structurally identical mesh hashes the same.
    assert first == topology.mesh_topology_signature(
        mesh_fixtures.build_mesh(2_500))


def test_foreach_get_path_matches_the_reference_implementation(mesh):
    assert (topology.mesh_topology_signature(mesh)
            == topology.reference_topology_signature(mesh))


def test_vertex_position_change_does_not_alter_the_signature(mesh):
    before = topology.mesh_topology_signature(mesh)
    mesh.move_vertex(7, 12.5)
    after = topology.mesh_topology_signature(mesh)
    assert before == after, "moving a vertex is not a topology change"
    assert after == topology.reference_topology_signature(mesh)


def test_changed_edge_structure_changes_the_signature(mesh):
    before = topology.mesh_topology_signature(mesh)
    mesh.drop_edge(3)
    assert topology.mesh_topology_signature(mesh) != before


def test_changed_polygon_structure_changes_the_signature(mesh):
    before = topology.mesh_topology_signature(mesh)
    mesh.swap_loop_vertices(0, 5)  # same counts, different loop->vertex map
    assert topology.mesh_topology_signature(mesh) != before


def test_vertex_order_change_changes_the_signature():
    """Reordering vertices rewrites the loop and edge index blocks."""
    mesh = mesh_fixtures.build_mesh(400)
    before = topology.mesh_topology_signature(mesh)
    mesh.swap_loop_vertices(1, 2)
    assert topology.mesh_topology_signature(mesh) != before


def test_counts_are_part_of_the_signature():
    small = mesh_fixtures.build_mesh(400)
    large = mesh_fixtures.build_mesh(2_500)
    assert (topology.mesh_topology_signature(small)
            != topology.mesh_topology_signature(large))


def test_schema_version_is_folded_in(mesh, monkeypatch):
    before = topology.mesh_topology_signature(mesh)
    monkeypatch.setattr(topology, "TOPOLOGY_SCHEMA_VERSION",
                        topology.TOPOLOGY_SCHEMA_VERSION + 1)
    assert topology.mesh_topology_signature(mesh) != before


def test_large_mesh_hashes_without_a_json_intermediate(monkeypatch):
    """The old path built lists of tuples and json.dumps'd them."""
    def explode(*_args, **_kwargs):
        raise AssertionError("topology hashing must not serialize to JSON")

    monkeypatch.setattr(json, "dumps", explode)
    big = mesh_fixtures.build_mesh(100_000)
    assert len(topology.mesh_topology_signature(big)) == 64


def test_peak_memory_stays_proportional_to_the_index_buffers():
    """Hashing a 100k-vertex mesh must not allocate a full Python copy of it."""
    big = mesh_fixtures.build_mesh(100_000)
    topology.mesh_topology_signature(big)  # warm any lazy imports

    tracemalloc.start()
    topology.mesh_topology_signature(big)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Four uint32 index blocks for a ~100k-vertex quad grid come to roughly
    # 6 MB. The old list-of-tuples + JSON route allocated tens of MB more; a
    # 16 MB ceiling catches any return to that shape while leaving headroom.
    assert peak < 16 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB is too high"


def test_uses_foreach_get_rather_than_iterating(mesh):
    counters = mesh.counters
    counters.reset()
    topology.mesh_topology_signature(mesh)
    assert counters.foreach_get_calls == 4
    assert counters.full_mesh_scans == 0, "the fast path must not iterate"


def test_reference_implementation_iterates(mesh):
    """Sanity: the oracle really does walk the collections it hashes."""
    counters = mesh.counters
    counters.reset()
    topology.reference_topology_signature(mesh)
    assert counters.foreach_get_calls == 0
    assert counters.full_mesh_scans > 0


def test_pin_signature_tracks_indices_and_vertex_count():
    base = topology.pin_indices_signature((1, 5, 9), vertex_count=100)
    assert base == topology.pin_indices_signature((1, 5, 9), vertex_count=100)
    assert base != topology.pin_indices_signature((1, 5), vertex_count=100)
    assert base != topology.pin_indices_signature((1, 5, 9), vertex_count=200)
    assert topology.pin_indices_signature((), vertex_count=100) != base


def test_geometry_fingerprint_combines_both_halves():
    combined = topology.geometry_fingerprint("topo", "pins")
    assert combined != topology.geometry_fingerprint("topo", "other")
    assert combined != topology.geometry_fingerprint("other", "pins")
    assert combined == topology.geometry_fingerprint("topo", "pins")
