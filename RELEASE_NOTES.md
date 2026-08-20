# Cloth NeXt 2.2.45

Cloth NeXt 2.2.45 safely repairs duplicate-vertex zero-area faces while keeping
intersection correction strictly fail-closed.

## Targeted degenerate repair

- Degenerate corrections no longer depend on Collision Gap or Surface Offset.
- Ordinary collinear triangles use the smallest robust local movement within
  the existing 2% edge-length limit.
- Diagnosed distinct vertex IDs at the same position may be welded through an
  explicit ID-to-ID target map. Auto Fix never searches a radius or runs Merge
  by Distance on a selection or object.
- Every weld is simulated first and rejected if it would affect unreported
  faces, incompatible attributes or groups, materials, duplicate faces,
  degenerates, or non-manifold geometry.

## Fail-closed intersection handling

- Candidate repairs are checked against relevant local triangles using the
  authoritative strict-crossing and coplanar-overlap diagnostics.
- Independent safe clusters are retained when another cluster is unsafe.
- Degenerate fixes can complete even when a reported self-intersection must be
  skipped.
- Auto Fix reports actual post-plan repairs rather than counting a vertex move
  as success.

## Real-scene verification

- Full Python suite: 1,440 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS built and validated the Windows Dev extension locally; all
  three packaged-artifact tests, the Companion scan, and the forbidden-solver
  material scan passed.
- Blender 5.2.0 LTS and Lumen repaired all 40 Top zero-area faces in the real
  `IntersectionTest.blend` scene through 15 explicit weld groups.
- The repair removed only the approved 33 redundant vertices, 75 edges, and 40
  diagnosed polygons. Surviving vertex positions did not move, duplicate faces
  stayed at zero, and non-manifold edges decreased by 30.
- Top's 36,666 pre-existing intersection pairs remained exactly unchanged.
- The isolated Shorts pair 15970 / 18393 remained safely skipped after all
  deterministic candidates within the 8% correction bound failed validation;
  Shorts geometry was unchanged.
- The external PPF Contact Solver is not bundled or modified.
