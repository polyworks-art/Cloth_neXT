"""Plot saved native trajectories and native force telemetry for both backends."""
import argparse
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpu',type=Path,required=True)
    p.add_argument('--cuda',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    names=['S04_NEON_Garments','S04_NEON_Garments.001','S04_NEON_Garments.002']
    fig,axes=plt.subplots(2,3,figsize=(14,7.5),layout='constrained')
    for root,backend,color in ((a.cpu,'CPU','#1565c0'),(a.cuda,'CUDA','#d45a0a')):
        report=json.loads((root/'report.json').read_text())
        cases={c['label']:c for c in report['cases']}
        summary=json.loads((root/'numerical-summary.json').read_text())
        hydro=next(c for c in summary['cases'] if c['label']=='hydro1')
        for column,name in enumerate(names):
            dry_folder=Path(cases['dry1'].get('artifact_root',root/'dry1'))
            dry=np.load(dry_folder/(name+'.npy')).astype(float)
            for label,style in (('hydro1','-'),('flow1','--')):
                folder=Path(cases[label].get('artifact_root',root/label))
                x=np.load(folder/(name+'.npy')).astype(float)
                rms=1000*np.sqrt(np.mean(np.sum((x-dry)**2,axis=-1),axis=1))
                axes[0,column].plot(np.arange(1,len(rms)+1),rms,style,color=color,
                    label=f'{backend}: '+('Hydro' if label=='hydro1' else 'Flow'),linewidth=1.6)
            loads=hydro['native_loads']
            axes[1,column].plot([v['evaluation_clock']['evaluation_time_s'] for v in loads],
                [v['objects'][name]['normal_force_magnitude_sum_n'] for v in loads],
                color=color,label=backend,linewidth=1.6)
    for column,name in enumerate(names):
        axes[0,column].set_title(name.replace('S04_NEON_',''))
        axes[0,column].set_xlabel('FLIP-Quellframe (1–30)')
        axes[0,column].set_xlim(1,30)
        axes[0,column].set_ylabel('Abweichung zu Dry: RMS [mm]')
        axes[1,column].set_xlabel('Nativer Kraft-Auswertungszeitpunkt [s]')
        axes[1,column].set_ylabel('Summe lokaler Normalkraftbeträge [N]')
        for row in (0,1):
            axes[row,column].grid(alpha=.22)
            axes[row,column].set_ylim(bottom=0)
        if all(np.all(line.get_ydata()==0) for line in axes[1,column].lines):
            axes[1,column].text(.5,.5,'0 N – keine direkte Benetzung',
                               ha='center',va='center',transform=axes[1,column].transAxes,fontsize=10)
    axes[0,0].legend(fontsize=8);axes[1,0].legend(fontsize=8)
    fig.suptitle('NEON: tatsächliche native CPU-/CUDA-Simulationen mit ursprünglichen Materialien\n'
                 '30 Ausgabeframes · CPU aus Checkpoint 19 fortgesetzt · experimentell, keine Produktfreigabe',fontsize=12)
    fig.savefig(a.output/'neon-cpu-cuda-validation.png',dpi=150)
    fig.savefig(a.output/'neon-cpu-cuda-validation.svg')
    plt.close(fig)


if __name__=='__main__':main()
