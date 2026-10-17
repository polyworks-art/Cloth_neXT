"""Export scientific plots of native force/wetting and temporal diagnostics."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();sys.path.insert(0,a.dependencies)
import matplotlib;matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
j=json.loads(a.input.read_text());rows=j['rows'];x=130+np.array([r['output_frame'] for r in rows]);fig,axes=plt.subplots(3,1,figsize=(10,8),sharex=True,layout='constrained')
series=[('normal_load_n','Normal load sum [N]'),('wet_area_m2','Wet cloth area [m²]'),('mean_wet_relative_speed_m_s','Mean wet relative speed [m/s]')]
sequence=json.loads(Path('docs/validation/gaia026/hydrodynamics/flip-original/grid-edge-130-160.json').read_text());bad=[r['frame'] for r in sequence['frames'] if r['reference_topology']['open_edges'] or r['reference_topology']['nonmanifold_edges']]
for ax,(key,label) in zip(axes,series):
 for frame in bad:
  if x.min()<=frame<=x.max():ax.axvspan(frame-.14,frame+.14,color='tab:orange',alpha=.18)
 ax.plot(x,[r[key] for r in rows],'.-',linewidth=1.3,color='#1675b8');ax.set_ylabel(label);ax.grid(alpha=.22)
axes[-1].set_xlabel('Exported Blender cloth frame');fig.suptitle('NEON 130–159: native CUDA diagnostics\nOrange bands: frames with original BOBJ boundary/non-manifold defects',fontsize=12)
a.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.output,dpi=170);fig.savefig(a.output.with_suffix('.svg'));plt.close(fig)
