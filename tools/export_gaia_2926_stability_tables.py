"""Export measured per-frame data and the five requested intervals, unchanged."""
import argparse
import csv
import json
from pathlib import Path

p = argparse.ArgumentParser(); p.add_argument('--analysis', type=Path, required=True); a = p.parse_args()
d = json.loads(a.analysis.read_text()); root = a.analysis.parent
rows = []
for pair in d['pairs']:
    for obj in pair['objects']:
        for frame in obj['frames']:
            rows.append(dict(left=pair['left'], right=pair['right'], object=obj['object'], **{k: v for k, v in frame.items() if k != 'vertex_indices_above_8mm'}))
with (root/'material-vertices-per-frame.csv').open('w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
intervals = []
for label, run in d['runs'].items():
    frames = {r['output']: r for r in run['frames']}
    for i, j in ((1, 2), (19, 20), (20, 21), (22, 23), (23, 24)):
        x, y = frames[i], frames[j]
        intervals.append(dict(run=label, interval=[i, j], scene_output_frames=[130+i, 130+j], evaluation_source_frames=[x['evaluation_source_frame'], y['evaluation_source_frame']], delta_force_n=y['force_n']-x['force_n'], delta_wet_m2=y['wet_m2']-x['wet_m2'], delta_mean_fluid_speed_m_s=y['mean_fluid_speed_m_s']-x['mean_fluid_speed_m_s'], delta_mean_relative_speed_m_s=y['mean_relative_speed_m_s']-x['mean_relative_speed_m_s'], collider_rms_speed_m_s=[x['collider_rms_speed_m_s'], y['collider_rms_speed_m_s']], wet_sensitivity_10um_m2=[x['wet_sensitivity_10um_m2'], y['wet_sensitivity_10um_m2']], convergence=[x['convergence'], y['convergence']], objects={name: dict(delta_force_n=y['objects'][name]['force_n']-x['objects'][name]['force_n'], delta_wet_m2=y['objects'][name]['wet_m2']-x['objects'][name]['wet_m2'], cloth_rms_speed_m_s=[x['objects'][name]['cloth_rms_speed_m_s'], y['objects'][name]['cloth_rms_speed_m_s']], normal_rotation_deg=[x['objects'][name]['wet_weighted_normal_rotation_deg'], y['objects'][name]['wet_weighted_normal_rotation_deg']]) for name in x['objects']}))
(root/'five-force-intervals.json').write_text(json.dumps(intervals, indent=2))
rows = []
for label, run in d['runs'].items():
    for frame in run['frames']:
        for name, values in frame['objects'].items():
            rows.append(dict(run=label, output=frame['output'], evaluation_source_frame=frame['evaluation_source_frame'], object=name, **values))
with (root/'native-forces-per-object.csv').open('w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
print('Per-frame vertex, force and interval tables exported')
