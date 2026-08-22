# VEYRA generalization audit

This audit records the production properties used by VEYRA after `e42f88c`.
Shorts and Top are regression inputs only; no production decision uses their
names, IDs, counts, dimensions, or expected results.

## Heuristics

| Area | Measured property | Units | Assumption and behavior outside the benchmark | Status |
|---|---|---|---|---|
| Weld proximity | Euclidean distance <= `max(1e-12, local edge scale * 1e-9)` | Scale-relative with a floating-point floor | Only numerical coincidence is considered; this is not a user-sized radius | Retained |
| Weld scope | Object UUID and diagnosed intersection vertex membership | Topological identity | Candidate discovery cannot cross objects or collect unrelated coincident geometry | Hardened |
| Weld semantics | Point attributes, vertex groups, face/corner data, materials, seam/sharp flags | Exact semantic equality/preservation | Unknown or conflicting data skips; no lossy reconciliation | Retained |
| Disconnected weld | Boundary chain correspondence and opposite surface continuation | Topological plus normalized directions | Object membership alone is not intent. Lining, stacked panels, pockets and isolated coincidences skip | Replaced unsafe one-sheet assumption |
| Topology transaction | Structural digest and strict authoritative contact reduction | Exact | Equal/increased/error/cancel outcomes restore the original mesh | Retained |
| Region construction | Connected contact-pair components scoped by object UUID | Topological | Storage order and object names do not affect geometry classification | Retained and tested |
| Sheet assignment | Two connected topological components with a consistent contact bipartition | Topological | Bipartite A-B-C is not accepted as two sheets; multi-sheet/fragmented regions skip | Hardened |
| Patch expansion | Two edge-adjacency rings, capped at 2048 triangles | Topological rings | Density changes physical patch width but not graph scope; the cap fails closed | Retained; cap remains a risk |
| Patch falloff | Ring distance plus one bounded neighbor smoothing pass | Dimensionless | Boundary remains fixed and weights remain in `[0,1]` | Retained |
| Directions | Sheet centroid delta and independent sheet normals | Normalized vectors | Rigid-transform equivariant; degenerate/contradictory direction evidence skips | Retained |
| Strengths | 1%, 2%, 4%, 8% of local mean edge scale | Scale-relative | All safe variants are evaluated; strongest is no longer assumed best | Hardened |
| Local validation | edge 0.80-1.20, area 0.60-1.50, orientation, finite coordinates, no new local crossings | Ratios | Fixed distortion budgets express safety rather than benchmark dimensions | Retained |
| Cumulative movement | 20% of region-local edge scale | Scale-relative | Bounds repeated movement independent of world scale | Retained |
| Candidate value | contact density, measured local crossing reduction, geometry margin, moved-vertex cost | Dimensionless | Explainable ordering; no learned weights or garment constants | Hardened |
| Batching | Disjoint vertex and triangle sets, same object, six-candidate cap | Topological | Overlapping work never shares a transaction; binary fallback isolates failures | Retained; cap is throughput policy |
| Cache | Job ID plus immutable object/topology fingerprint | Identity hash | A changed topology or job cannot reuse stale coordinates | Retained |
| Authority | Fresh external Lumen count after each installed transaction | Count | Local estimates never accept a repair; strict decrease is mandatory | Retained |

## Corpus matrix

| Case | Structural purpose | Expected safety result | Evidence |
|---|---|---|---|
| A clean cloth | No problem/no-op invariant | No candidates or modifications | `test_clean_cloth_has_no_weld_and_no_region_candidates` |
| B duplicate seam | Two patches forming one continuous sheet | Two explicit-ID welds | `test_exact_duplicate_continuous_boundary_is_weldable` |
| C intentional layers | Same-side stacked panels | Protected/skip | `test_intentional_stacked_layers_fail_closed` |
| D near duplicates | Separations across local-scale ratios | No radius merge | `test_near_duplicate_layers_are_not_radius_merged` |
| E true two-sheet contact | Disconnected two-sheet region graph | Region candidates, no weld | `two_sheet_value` and region tests |
| F folded sheet/cuff | Ambiguous continuation | Protected/skip | frozen holdout cuff |
| G dense/branching contact | More than two topological sheets | Ambiguous/skip | three-sheet tests |
| H three independent regions | Ordering and batching | Stable disjoint batch schedule | `test_multiple_independent_regions_batch_deterministically` |
| I mesh density | Increasing independent triangle/contact sets | Bounded planning and graph-local behavior | planning scaling smoke |
| J scene scale | 0.01x, 1x, 100x | Equivalent classification and scaled displacement | weld/region scale tests |
| K UV/material/seam | Corner and face semantic discontinuity | Blender transaction protection/skip | weld pipeline and semantic preservation tests |
| L groups/pins | Different point signature | Protected/skip | `test_point_attribute_or_constraint_conflict_protects_whole_seam` |
| M shape keys | Topology cardinality shared by key blocks | Blender preflight rejection | `_veyra_safe_weld_transaction` guard |
| N linked/shared data | External/shared datablock | Blender preflight rejection | library/users guards |
| O multiple objects | Coincident indices/positions in distinct UUIDs | Never cross-object weld or region | object-identity region and weld tests |

## Constants and provenance

The four displacement strengths are logarithmic probes of local edge scale, not
world distances. Distortion limits are conservative geometry invariants. The
two-ring patch and six-member batch caps bound work and blast radius; neither
changes weld classification. No constant was selected to reproduce a known
Shorts/Top count.

## Known risks

- Geometrically and semantically indistinguishable intent cannot be inferred.
  Such cases must continue to skip unless stronger source metadata is added.
- The two-ring patch can under-repair very broad penetrations; increasing it
  without a locality proof would increase the damage radius.
- The region solver skips rather than subdivides a multi-sheet component. This
  is safe but leaves repair opportunity on the table.
- Mean local edge scale can be influenced by strongly irregular triangulation;
  the distortion checks and authoritative rollback remain the safety backstop.
