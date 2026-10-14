"""Summarize real PC2 histories and render inspection plots (read-only)."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from cloth_next.bake.pc2 import iter_frames

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--matrix',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    matrix=json.loads(a.matrix.read_text(encoding='utf-8-sig'))
    records=[]
    for case in matrix['cases']:
        path=Path(case.get('report',''))
        if not path.is_file():continue
        report=json.loads(path.read_text())
        if not report.get('objects'):continue
        for obj in report['objects']:
            if not Path(obj['pc2']).is_file():
                records.append(dict(label=case['label'],object=obj['object'],
                    result='UNAVAILABLE',reason='Earlier rebake replaced original PC2; validated scalar checks retained'))
                continue
            samples=np.asarray(list(iter_frames(Path(obj['pc2']))),dtype=float)
            nx,ny={1000:(25,40),5000:(50,100),20000:(100,200),50000:(200,250)}[obj['vertices']]
            speed=np.linalg.norm(np.diff(samples,axis=0)*report.get('fps',24),axis=2)
            histories=dict(label=case['label'],object=obj['object'],frames=len(samples),
                speed_max_per_frame_m_s=speed.max(axis=1).tolist(),
                speed_p99_per_frame_m_s=np.quantile(speed,.99,axis=1).tolist(),
                centroid_per_frame=samples.mean(axis=1).tolist())
            records.append(histories)
            fig=plt.figure(figsize=(12,7))
            for panel,index in enumerate(sorted(set([0,len(samples)//2,len(samples)-1]))):
                ax=fig.add_subplot(2,3,panel+1,projection='3d')
                grid=samples[index].reshape(ny,nx,3)
                for j in np.unique(np.linspace(0,ny-1,20).astype(int)):
                    line=grid[j];ax.plot(*line.T,color='#338ac0',linewidth=.5)
                for i in np.unique(np.linspace(0,nx-1,20).astype(int)):
                    line=grid[:,i];ax.plot(*line.T,color='#338ac0',linewidth=.5)
                ax.scatter(*grid[0].T,color='#d85645',s=3)
                ax.set_title(f'Frame {index+1}');ax.set_box_aspect((1,1,1))
                limits=np.stack([samples.min(axis=(0,1)),samples.max(axis=(0,1))])
                for axis,setter in enumerate((ax.set_xlim,ax.set_ylim,ax.set_zlim)):
                    setter(limits[0,axis]-.03,limits[1,axis]+.03)
            ax=fig.add_subplot(2,1,2)
            ax.plot(np.arange(2,len(samples)+1),speed.max(axis=1),label='Max speed')
            ax.plot(np.arange(2,len(samples)+1),np.quantile(speed,.99,axis=1),label='99th percentile')
            ax.set(xlabel='Frame',ylabel='m/s');ax.legend();ax.grid(alpha=.2)
            fig.suptitle(case['label']+' / '+obj['object']+' / native PC2')
            fig.tight_layout();fig.savefig(a.output/(case['label']+'-'+obj['object']+'.png'),dpi=110)
            plt.close(fig)
    (a.output/'pc2-histories.json').write_text(json.dumps(records,indent=2))

if __name__=='__main__':main()
