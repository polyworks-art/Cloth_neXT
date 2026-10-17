# GAIA practical reconstruction acceptance — prospective experiment plan

2026-10-16. This plan implements the user's revised artist-oriented acceptance request.
Historical 10 µm / identical-connectivity results remain untouched as geometric
reproduction diagnostics, not as a new physical or visual release gate.
No production enablement or release is authorized by this plan.

## Metrics and scale

Native FLIP dx = 16.0284 mm; original surface lattice = 8.0142 mm. Existing
transport fine SDF = 7.82636 mm; coarse sensitivity SDF = 15.6527 mm. Garments
retain original world scale, materials, 186 attachments, Cn=1, Ct=0 and one-way coupling.
The previously reported 2.49165 mm is bidirectional nearest-vertex distance.
It is not a triangle surface distance, Hausdorff bound, or implicit residual.
Sampled point-to-triangle distances are reported separately, including sampling limits.
A radius residual against particles is only a particle-support diagnostic.

## Prospective decision rules (before sensitivity solves)

- Geometry: deterministic grid-edge join, no invented triangles/particles, no radius
  adjustment, no removed components. Report all topology changes and zero-area faces.
  Distances alone do not reject a candidate; changes near cloth require physical tests.
- Local occupancy/wetting: on identical frozen cloth quadrature compare exact closed
  reference to candidate and SDF resolutions. Target misclassified weighted area <=
  max(1% total cloth area, 5% reference wet area), per reference frame. Report signed
  and unsigned errors separately; cancellation cannot hide wrong occupancy.
- Physical response: candidate vs valid-reference inputs target wet-area and normal-load
  differences <=5% (absolute floors 0.005 m² and 1 N), with velocity payloads unchanged.
  Establish the native repeatability floor with two unmodified replays before attributing
  trajectory or force changes. Compare conservative SDF offsets ±2.5 mm and coarse SDF;
  these are sensitivity perturbations ONLY, never an adopted water enlargement/thinning.
- Visual trajectory screening: same native scene, target cloth RMS displacement <=2 mm
  and maximum <=8 mm relative to baseline (about one surface cell), interpreted alongside
  baseline repeatability. This numerical screen does not replace visual review.
- Temporal/numerical stability: all values finite, no SDF-guard violation, no unexplained
  alternating force jumps. Compare native force differences and first differences to
  baseline and correlate changes with wet area/relative velocity; an unexplained excess
  >10% of reference mean normal load requires investigation, not silent acceptance.
- End-to-end: short previously-invalid source bake first, then regular Blender 130–159
  with real160 lookahead, unchanged original scene settings, PC2 reload, independent
  reciprocal collider audit, native diagnostics and resource capture. Any actual collider
  crossing, invalid transport or failed operator prevents end-to-end acceptance.

The coarse grid and ±2.5mm tests may fail these targets: that characterizes sensitivity
and selects supported resolution; it is not permission to weaken thresholds or alter
water geometry. If native repeatability exceeds a target, record an inconclusive metric
and investigate the solver rather than automatically passing the reconstruction.
