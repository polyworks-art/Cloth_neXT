# Cloth NeXt 2.2.46

Cloth NeXt 2.2.46 presents every safely detectable geometry problem in one
preflight and keeps the diagnostic overlay current after Auto Fix.

## Complete geometry preflight

- Degenerate faces no longer hide intersections until a second Bake attempt.
- Every deformable is scanned before invalid geometry blocks solver startup.
- Degenerates and mapped intersections share one authoritative diagnostic
  result and appear together in the existing compact viewport presentation.
- Degenerate triangles are excluded from intersection candidates, so counts
  remain accurate.

## Exact and scalable intersection detection

- Blender's BVH provides the broad phase instead of an all-pairs scan.
- Candidate pairs use Cloth NeXt's existing strict-crossing and
  coplanar-overlap predicates.
- Identical triangles, normal mesh adjacency, and shared source vertices are
  excluded from self-intersection results.
- Cross-object deformable intersections can be represented without confusing
  object-local vertex indices.

## Fresh diagnostics after Auto Fix

- Safely diagnosed duplicate-position degenerates continue to use explicit
  local weld target maps—never a radius-based Merge by Distance.
- Supported intersections continue to use bounded, validated nudging.
- Auto Fix clears the pre-repair snapshot, rebuilds current Triangle IDs, and
  performs a local recheck without starting a Bake or Lumen.
- Remaining unsafe intersections stay visible immediately after repair.

## Verification

- Full Python suite: 1,447 passed, 9 skipped, 3 deselected.
- Targeted geometry and UI suite: 186 passed.
- Blender 5.2.0 LTS tested the updated real Top-and-Shorts scene. Its current
  first pass found 18 degenerates, blocked solver startup, and reduced 62,462
  triangles to 14,167 BVH candidates and 122 exact narrow-phase tests.
- Auto Fix repaired all 18 current Top degenerates through explicit local
  welds. A new post-fix snapshot reported no stale IDs, no degenerates, and no
  exact local intersections; Shorts remained unchanged.
- The external PPF Contact Solver is not bundled or modified.
