"""Preserve source audit and the explicit reconstruction NO-GO decision."""
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/validation/gaia026/hydrodynamics'
CACHE = Path('F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache')


def main():
    rows = []
    for name in ('1-129', '130-160', '161-250'):
        rows += json.loads((OUT / f'surface-sources-{name}.json').read_text())
    assert [r['frame'] for r in rows] == list(range(1, 251))
    valid = lambda r: r['watertight'] and r['winding'] and not r['degenerate_triangles'] and r['volume_m3'] > 0
    intervals = [dict(first=i+1, last_output=i+30, lookahead=i+31) for i in range(220)
                 if all(valid(r) for r in rows[i:i+31])]
    inventory = dict(extension_counts=dict(Counter(p.suffix for p in CACHE.rglob('*') if p.is_file())),
        savestates={p.parent.name: json.loads(p.read_text()) for p in (CACHE/'savestates').rglob('*.state')},
        native_liquid_level_set_available=False,
        note='No cached SDF/MAC grid found; .ffp3/.fpd and .data contain particle records, not a native liquid level set.')
    (OUT/'surface-source-inventory.json').write_text(json.dumps(inventory, indent=2))
    (OUT/'surface-valid-30-frame-intervals.json').write_text(json.dumps(intervals, indent=2))
    mid = rows[129:160]
    lines = ['# GAIA surface reconstruction investigation — NO-GO', '',
        'Read-only source audit completed. No reconstructed SDF was accepted or connected to the solver. '
        'The requested new short bake and 130–159 bake were not run because physical equivalence is unproven. '
        'The previous successful 131–133 CUDA bake and all CPU/CUDA artifacts are preserved.', '',
        '## Frames 130–160', '',
        '|Frame|Closed components|Open edges|Non-manifold edges|Zero-area triangles|Simple boundary loops|',
        '|---|---:|---:|---:|---:|---:|']
    for r in mid:
        lines.append('|'+ '|'.join(str(r[k]) for k in ('frame','closed_components','open_edges','nonmanifold_edges','degenerate_triangles','boundary_loops'))+'|')
    lines += ['', 'Disconnected closed components are valid. Zero simple boundary loops does not imply closure: '
        'branched or interrupted boundary graphs are recorded separately in the JSON. '
        'Closed-component counts are edge-topology counts, not proof of absence of self-intersections.', '',
        '## Root-cause evidence', '']
    for frame in (130, 135):
        r = rows[frame-1]
        lines.append(f"Frame {frame}: {r['open_edges']} open and {r['nonmanifold_edges']} non-manifold edges; "
            f"problem regions: `{json.dumps(r['problem_regions'])}`. These are internal topological defects, "
            'not ordinary disconnected water bodies or evidence permitting domain caps. '
            'The exact upstream mesher defect cannot be established from the exported mesh alone.')
    lines += ['', '## Reconstruction decision', '',
        'All 31 requested samples have structurally valid matching FFP3 position/velocity layouts. '
        'The cache inventory and all seven savestates expose particles, but no saved liquid SDF or MAC grid. '
        'The existing GAIA velocity/support field is not a liquid level set. '
        'The installed FLIP wrapper exposes surface output and simulation update, but no standalone cached liquid-SDF export.', '',
        'A diagnostic particle-union candidate uses all ~8.78 million fluid particles and the original meshing radius '
        '`dx*(3/(32*pi))^(1/3)*3 = 0.014914832 m`; no radius fitting, hole filling, component deletion or domain capping. '
        'Original settings: dx 0.016028393 m, surface subdivisions 1 (wrapper adds 1), smoothing 0.5 / 2 iterations, '
        'obstacle meshing offset enabled. The source mesher also consumes a solid SDF. '
        'The diagnostic has not reproduced those solid boundaries, smoothing or polygonization.', '',
        'The candidate is evaluated analytically with nearest-particle queries, without a reconstruction grid. '
        'On 20,000 deterministic vertex samples per reference frame 131–133 its scalar residual RMS is '
        '5.28–5.43 mm, p95 absolute 10.90–10.99 mm, maximum absolute 13.77–14.22 mm. '
        'This is a residual of the sphere-union implicit field, not a symmetric Hausdorff surface error or a true interior SDF distance. '
        'The negative mean (−3.19 to −3.32 mm) reveals a directional occupancy bias at reference surface samples. '
        'Nearest-particle velocities are documented, but are not accepted as the transport velocity estimator.', '',
        '**NO-GO:** there is no validated reconstructed surface, wet-cloth-area/force equivalence, '
        'independent occupancy acceptance or resolution convergence. No cell size is approved; '
        'a future grid could lose subcell droplets, gaps and sheets. These quantities cannot be reported as passed. '
        'No new force or trajectory result is fabricated. The existing SDF bounds guard, Cn=1/Ct=0, '
        'one-way coupling, materials and original sources are unchanged.', '',
        '## Other intervals and preserved regression evidence', '',
        f'{len(intervals)} topologically valid 30-output-frame intervals with real next-frame lookahead were found; '
        f'first: `{intervals[0] if intervals else None}`. These are separate possible tests; none replaces 130–159.', '',
        'The previous 131–133 reference remains the only completed middle-sequence Blender test: '
        '98.868/106.324 N sampled normal loads, 0.64358/0.62193 m² wet area, main-cloth hydro/dry max 56.248 mm, '
        'native/PC2 max 6.102e-8 m, exact evaluated playback after reload, zero audited collider crossings. '
        '357 prior add-on tests passed. Previous native CPU/CUDA regressions remain unchanged; '
        'the strict state-dependent force comparison remains 19/20, not silently accepted. '
        'No native code or binaries changed during this investigation; no new native regression is claimed.', '',
        'Release blockers: authoritative/reproducible particle-to-surface treatment including obstacle boundaries; '
        'independent occupancy, wet-area and force validation; resolution sensitivity; then new short bake and full '
        '130–159 +160 end-to-end validation. No source mutation, version change, commit, push or release.', '',
        'Upstream implementation references: '
        '[particle mesher](https://github.com/rlguy/Blender-FLIP-Fluids/blob/master/src/engine/particlemesher.cpp), '
        '[fluid simulation](https://github.com/rlguy/Blender-FLIP-Fluids/blob/master/src/engine/fluidsimulation.cpp). '
        'These explain the candidate only; current upstream is not asserted byte-identical to the cached simulation build.', '']
    (ROOT/'docs/GAIA_SURFACE_RECONSTRUCTION_INVESTIGATION.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
