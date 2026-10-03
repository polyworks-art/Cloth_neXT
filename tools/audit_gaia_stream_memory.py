# SPDX-License-Identifier: GPL-3.0-or-later
"""Measure 10/250 prepared-field runtime windows in separate fresh processes."""
import argparse
import gc
import json
from pathlib import Path
import sys
import time
import weakref
import numpy as np
import psutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.streaming import WaterStreamReader

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--frames',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with GaiaContainer(args.cache) as container:
        water=container.manifest['water'];first=container.manifest['frames'][0]['frame']
    process=psutil.Process();initial=process.memory_info();samples=[];refs=[]
    started=time.perf_counter()
    with WaterStreamReader(args.cache,first=first,last=first+args.frames-1,
                           fps=water['fps'],expected_fingerprint=water['fingerprint']) as reader:
        for boundary in range(args.frames):
            window=reader.window(boundary);refs.append(weakref.ref(window.values))
            samples.append(dict(reader.last_window_timings));del window
        gc.collect()
        assert all(ref() is None for ref in refs)
    final=process.memory_info()
    report={'frames':args.frames,'seconds':time.perf_counter()-started,
            'baseline_rss':initial.rss,'final_rss':final.rss,
            'peak_rss':getattr(final,'peak_wset',final.rss),
            'mean_window_seconds':{key:float(np.mean([s[key] for s in samples])) for key in samples[0]},
            'all_windows_released':True}
    args.output.write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)

if __name__=='__main__':main()
