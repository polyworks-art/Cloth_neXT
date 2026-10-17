"""Plot measured native repeatability without smoothing or acceptance changes."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p = argparse.ArgumentParser(); p.add_argument('--analysis', type=Path, required=True); p.add_argument('--output', type=Path, required=True); a = p.parse_args()
d = json.loads(a.analysis.read_text()); a.output.mkdir(parents=True, exist_ok=True)
labels = {'published-2926-full-host': '2.9.26 A', 'repeat-b': '2.9.26 B', 'repeat-c': '2.9.26 C', 'bake-long-r7': '2.9.25 reference'}
fig, axes = plt.subplots(3, 2, figsize=(13, 10), sharex=True)
for pair in d['pairs']:
    label = labels.get(pair['left'], pair['left'])+' / '+labels.get(pair['right'], pair['right'])
    style = '--' if 'bake-long-r7' in (pair['left'], pair['right']) else '-'
    for row, obj in enumerate(pair['objects']):
        frames = obj['frames']; x = [f['frame'] for f in frames]
        axes[row, 0].plot(x, [f['rms_m']*1000 for f in frames], style, label=label)
        axes[row, 1].plot(x, [f['maximum_m']*1000 for f in frames], style, label=label)
        axes[row, 0].set_ylabel(obj['object']+'\nmm'); axes[row, 1].axhline(8, color='.5', lw=.5)
for ax in axes.ravel(): ax.grid(alpha=.2)
axes[0, 0].set_title('Corresponding material vertices: RMS'); axes[0, 1].set_title('Maximum (8 mm investigation screen)')
axes[-1, 0].set_xlabel('NEON scene frame'); axes[-1, 1].set_xlabel('NEON scene frame')
axes[0, 0].legend(fontsize=6)
fig.suptitle('Native world-space measurements; vertex displacement is not surface distance')
fig.tight_layout(); fig.savefig(a.output/'material-vertex-repeatability.png', dpi=160); plt.close(fig)
fig, axes = plt.subplots(4, 1, figsize=(12, 11), sharex=True)
for label, run in d['runs'].items():
    rows = run['frames']; x = [r['output'] for r in rows]
    for ax, key in zip(axes, ('force_n', 'wet_m2', 'mean_fluid_speed_m_s', 'mean_relative_speed_m_s')):
        ax.plot(x, [r[key] for r in rows], '--' if label == 'bake-long-r7' else '-', label=labels.get(label, label))
for ax, title in zip(axes, ('Normal load (N)', 'Wetted area (m²)', 'Wet-area weighted fluid speed (m/s)', 'Wet-area weighted relative speed (m/s)')):
    ax.set_ylabel(title); ax.grid(alpha=.2)
    for k in (2, 20, 21, 23, 24): ax.axvline(k, color='.6', lw=.5)
axes[0].legend(fontsize=8); axes[-1].set_xlabel('Native output index; exact evaluation clocks recorded in analysis.json')
fig.suptitle('Unsmoothed final native evaluations; these are not substep-integrated forces')
fig.tight_layout(); fig.savefig(a.output/'native-force-and-wetting.png', dpi=160); plt.close(fig)
