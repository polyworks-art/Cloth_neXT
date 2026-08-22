# Cloth NeXt 2.2.47

Cloth NeXt 2.2.47 introduces Veyra, a dedicated Companion workflow that safely
reduces large groups of solver-confirmed garment intersections without starting
the frame simulation.

## Veyra region repair

- Veyra groups mapped self-intersections into deterministic, object-bound
  regions and builds small two-ring repair patches.
- Adaptive 1%, 2%, 4%, and 8% candidates are checked locally; only the
  strongest safe candidate proceeds to authoritative Lumen validation.
- Independent regions may be applied as one transaction only when affected
  vertices and expanded triangle patches are disjoint.
- Every authoritative result must strictly lower the global intersection
  count. Equal or higher results, cancellation, and exceptions restore exact
  saved coordinates.
- Ambiguous sheet assignments remain fail-closed and are never guessed.

## Persistent validation session

- One Veyra Companion job and PID remains active through Analyze, Solve, Apply,
  Revalidate, and Validate Contacts.
- Immutable topology, adjacency, object identity, collision settings, Params,
  and export structure are retained for the repair operation.
- Follow-up passes refresh compact vertex-position state instead of rebuilding
  the complete evaluated scene and topology.
- Artist-facing progress stays responsive without intermediate error flashing,
  readiness loops, or Companion restarts.

## Diagnostic consistency

- Solver totals, detailed pairs, mapped pairs, overlays, and Bake-window state
  now retain the same authoritative contact result.
- Expected Veyra contact findings are presented as measurements rather than
  transient Bake failures.
- Cleanup releases topology and validation caches after success, cancellation,
  and failure.

## Real-scene result

- The real Shorts case improved monotonically from 2,129 to 1,224
  intersections: 905 repaired, or 42.51%.
- Three accepted batch transactions produced the same authoritative chain in
  every measured run: `2129 -> 1777 -> 1584 -> 1224`.
- Lumen BUILD calls fell from 7 to 4. Total measured Veyra time was 79.61 s,
  92.31 s, and 106.30 s, compared with the previous 162.14 s baseline.
- All 18 Top degenerates were repaired through the existing exact local weld
  path. Shorts topology remained unchanged and only vertex positions moved.
- No frame simulation started, the Companion PID was reused, and the final UI
  state was `FINISHED`.

## Verification

- Full Python suite: 1,501 passed, 9 skipped, 3 deselected.
- Blender 5.2.0 LTS and the real Lumen solver completed three reproducible
  validation runs with no accepted crossing regression or rollback drift.
- The original `IntersectionTest.blend` remained byte-identical with SHA-256
  `E88FACD6AB6F9A1805160D2DF4539C2971806088278F711D32FA2CB9904C41E5`.
- The external PPF Contact Solver is not bundled or modified.
