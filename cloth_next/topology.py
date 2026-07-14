# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic, allocation-bounded mesh topology hashing (never imports ``bpy``).

The digest identifies the *connectivity* of a mesh, never its shape: moving a
vertex leaves the signature untouched, while changing the edge/polygon/loop
structure — or merely reordering vertices — changes it.

Two implementations produce byte-identical digests:

* :func:`mesh_topology_signature` — the production path. It pulls each index
  block straight into a preallocated little-endian ``uint32`` array via
  Blender's ``foreach_get`` and feeds the raw buffers to SHA-256 through a
  ``memoryview``, so no Python list, no JSON string, and no second copy of the
  topology is ever materialized.
* :func:`reference_topology_signature` — a small, obviously-correct Python
  implementation kept as the executable specification. Tests assert the two
  agree, and it is the fallback when NumPy is unavailable.

Peak memory is 4 bytes per index (roughly 12 MB for a 500k-vertex quad grid)
instead of the hundreds of megabytes the previous list-of-tuples + JSON route
allocated.
"""

from __future__ import annotations

import hashlib
import struct

try:  # pragma: no cover - NumPy ships with Blender and the solver runtime
    import numpy
except ImportError:  # pragma: no cover
    numpy = None

# Bump when the hashed field set or byte layout changes. It is folded into the
# digest, so an older signature can never collide with a newer one.
TOPOLOGY_SCHEMA_VERSION = 1

_HEADER = struct.Struct("<5I")
_UINT32 = "<u4"


def _counts(mesh) -> tuple[int, int, int, int]:
    return (len(getattr(mesh, "vertices", ())),
            len(getattr(mesh, "edges", ())),
            len(getattr(mesh, "polygons", ())),
            len(getattr(mesh, "loops", ())))


def _header(counts: tuple[int, int, int, int]) -> bytes:
    return _HEADER.pack(TOPOLOGY_SCHEMA_VERSION, *counts)


def mesh_topology_signature(mesh) -> str:
    """SHA-256 over the connectivity of ``mesh`` using ``foreach_get`` buffers.

    Only ever called from a full validation (bake, explicit validate, or the
    debounced validation timer) — never from a ``Panel.draw``.
    """
    if numpy is None:  # pragma: no cover - exercised only without NumPy
        return reference_topology_signature(mesh)

    counts = _counts(mesh)
    _vertices, edge_count, polygon_count, loop_count = counts
    digest = hashlib.sha256()
    # The vertex count enters the digest through the header rather than as a
    # block of its own — positions are deliberately not hashed.
    digest.update(_header(counts))

    for collection, attribute, length in (
            (mesh.edges, "vertices", edge_count * 2),
            (mesh.polygons, "loop_start", polygon_count),
            (mesh.polygons, "loop_total", polygon_count),
            (mesh.loops, "vertex_index", loop_count)):
        if length == 0:
            continue
        buffer = numpy.empty(length, dtype=_UINT32)
        collection.foreach_get(attribute, buffer)
        # memoryview keeps SHA-256 reading the buffer in place: no bytes copy.
        digest.update(memoryview(buffer).cast("B"))
    return digest.hexdigest()


def reference_topology_signature(mesh) -> str:
    """Executable specification of :func:`mesh_topology_signature`.

    Deliberately simple and slow: it walks the collections in Python. Tests
    pin the fast path to this, so a future ``foreach_get`` change that alters
    the digest cannot pass unnoticed.
    """
    counts = _counts(mesh)
    digest = hashlib.sha256()
    digest.update(_header(counts))

    def _block(values) -> None:
        chunk = bytearray()
        for value in values:
            chunk += struct.pack("<I", int(value))
        digest.update(bytes(chunk))

    edge_indices = []
    for edge in getattr(mesh, "edges", ()):
        edge_indices.extend(int(index) for index in edge.vertices)
    _block(edge_indices)
    _block(int(polygon.loop_start) for polygon in getattr(mesh, "polygons", ()))
    _block(int(polygon.loop_total) for polygon in getattr(mesh, "polygons", ()))
    _block(int(loop.vertex_index) for loop in getattr(mesh, "loops", ()))
    return digest.hexdigest()


def pin_indices_signature(indices, *, vertex_count: int) -> str:
    """Digest of a validated pin-index set, bound to the mesh it was taken from."""
    digest = hashlib.sha256()
    digest.update(_HEADER.pack(TOPOLOGY_SCHEMA_VERSION, vertex_count,
                               len(indices), 0, 0))
    if indices:
        if numpy is not None:
            buffer = numpy.asarray(indices, dtype=_UINT32)
            digest.update(memoryview(buffer).cast("B"))
        else:  # pragma: no cover
            digest.update(b"".join(struct.pack("<I", int(index))
                                   for index in indices))
    return digest.hexdigest()


def geometry_fingerprint(topology_signature: str, pin_signature: str) -> str:
    """The expensive half of the Bake fingerprint: topology plus pin indices."""
    return hashlib.sha256(
        f"{TOPOLOGY_SCHEMA_VERSION}\0{topology_signature}\0{pin_signature}"
        .encode("utf-8")).hexdigest()
