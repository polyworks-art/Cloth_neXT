"""Read-only source-time interpolation and exact scene-time PC2 proof."""
import sys,json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.bake.pc2 import write_pc2,iter_frames,read_header
from cloth_next.ppf.coordinates import ppf_position_to_blender
from tools.audit_water_response import sample
from frontend import App
OUT=ROOT/'dist/water-push-cadence'

def run():
    App.set_backend('cuda')
    source=Path(r'F:\16_Cloth_neXt\cache_AD\Plane_WaterFlow.gaia')
    data=np.load(ROOT/'dist/water-response/real-cloth.npz')
    point=data['positions'][49,520:521].astype(float)
    with GaiaContainer(source) as cache:
        a=cache.frame(49);b=cache.frame(50)
        va=sample(a.contribution,np.array(a.minimum),np.array(a.maximum),point)[0]
        vb=sample(b.contribution,np.array(b.minimum),np.array(b.maximum),point)[0]
        raw_a=sample(a.velocity,np.array(a.minimum),np.array(a.maximum),point)[0]
        raw_b=sample(b.velocity,np.array(b.minimum),np.array(b.maximum),point)[0]
        mask_a=float(sample(a.influence,np.array(a.minimum),np.array(a.maximum),point)[0])
        mask_b=float(sample(b.influence,np.array(b.minimum),np.array(b.maximum),point)[0])
        ta=float(cache.manifest['frames'][49]['time']);tb=float(cache.manifest['frames'][50]['time'])
        water=[]
        for hz in [24,48,60,96,120]:
            for tick in range(int(np.ceil(ta*hz)),int(np.floor(tb*hz))+1):
                time=tick/hz;alpha=(time-ta)/(tb-ta)
                if alpha<-1e-10 or alpha>1+1e-10:continue
                water.append({'hz':hz,'time':time,'source_frames':[50,51],'alpha':alpha,'encoded_water_contribution_m_s':((1-alpha)*va+alpha*vb).tolist(),'raw_velocity_m_s':((1-alpha)*raw_a+alpha*raw_b).tolist(),'support':(1-alpha)*mask_a+alpha*mask_b})
    playback=[]
    for report_path in OUT.glob('control-discrete-*.json'):
        if report_path.name.endswith('-progress.json'):continue
        report=json.loads(report_path.read_text());hz=report['control_hz']
        if hz==24:continue
        app=App.load(Path(report['native_root']).name)
        fixed=app.session.select('cadence').fixed_session
        if fixed is None:raise RuntimeError('Public saved-session API unavailable')
        duration=report['duration'];frames=[];identities=[]
        # 60 Hz is not a multiple of 24: select only common exact timestamps.
        # It cannot provide every scene sample without an explicit resampling.
        for frame in range(int(duration*24)+1):
            native=frame*hz/24
            if native!=round(native):continue
            positions,identity=fixed.get.vertex(int(native))
            assert identity==native
            frames.append(np.array([ppf_position_to_blender(v) for v in positions],dtype='f4'));identities.append(frame)
        if hz%24:
            playback.append({'hz':hz,'dt':report['dt'],'exact_scene_frames':identities,'all_scene_samples_available':False,'reason':'60/24 is nonintegral; use a common output grid such as 120 Hz, with separate 60 Hz holds, or explicitly resample'})
            continue
        path=OUT/f'playback-{hz}-{report["dt"]}.pc2'
        write_pc2(path,frames,start_frame=1.,sample_rate=1.)
        header=read_header(path);decoded=list(iter_frames(path))
        assert len(decoded)==len(frames)
        error=max(float(np.max(np.abs(a-b))) for a,b in zip(decoded,frames))
        playback.append({'hz':hz,'dt':report['dt'],'all_scene_samples_available':True,'frame_count':header.frame_count,'maximum_position_error':error,'maximum_time_error':max(abs(i/24-(i*(hz//24))/hz) for i in identities),'pc2':str(path),'start_blender_frame':header.start_frame,'sample_rate_scene_frames':header.sample_rate})
    result={'source':str(source),'source_fps':24,'source_pair':[50,51],'source_times':[ta,tb],'source_particle_bake_unchanged':True,'interpolation':water,'playback':playback}
    (OUT/'timing.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':run()
