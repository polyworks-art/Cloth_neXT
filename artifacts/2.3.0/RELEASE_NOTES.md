# Cloth NeXt 2.3.0 Beta

Cloth NeXt 2.3.0 introduces VEYRA, a guided way to analyze and safely reduce
cloth self-intersections before baking. This is a Beta release: use a copy or
versioned save of important production scenes while the workflow receives
broader real-world testing.

## Analyze, repair, validate

- VEYRA finds solver-confirmed contact regions, repairs only changes that pass
  local mesh safety checks, and validates every accepted step again with the
  selected solver.
- The Veyra window stays open as one continuous job. Progress moves through
  analysis, repair, and validation without flashing an error or restarting
  between successful passes.
- Results clearly report repaired topology, remaining intersections, and areas
  skipped because they could not be changed safely.
- VEYRA never starts the normal frame simulation automatically.

## Safety first

- Exact local welds are limited to diagnosed duplicate-position vertices.
  Cloth NeXt does not run a global Merge by Distance.
- Intentional layers, lining, patches, folded cloth, near duplicates, and
  ambiguous multi-sheet regions are preserved when repair intent cannot be
  proven.
- Vertex groups and pin weights, attributes, materials, UV data, seam and sharp
  flags, Shape Keys, linked data, and shared meshes are protected by fail-closed
  checks.
- A repair is kept only when a fresh global solver check reports fewer contacts.
  Failed, cancelled, unchanged, or worse candidates are rolled back exactly.

## Stability and compatibility

- Companion startup, reuse, cancellation, terminal shutdown, and Blender-exit
  cleanup have been hardened so repeated operations do not leave stale jobs or
  orphan windows.
- Geometry diagnostics now collect issues across all Cloth objects and refresh
  overlays after topology changes.
- Both currently supported solver profiles are retained: Velune (protocol 0.13)
  and Lumen (protocol 0.18). The solver remains a separate installation and is
  not included in the Cloth NeXt extension.

## Known Beta limitation

VEYRA is deliberately conservative. Complex or ambiguous regions can remain
unrepaired when Cloth NeXt cannot prove that a change is safe. A partial result
with fewer intersections is expected in such cases; review the highlighted
remaining contacts before starting the normal bake.
