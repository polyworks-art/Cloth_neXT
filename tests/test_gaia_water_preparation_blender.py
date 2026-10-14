import sys
import threading
import time
from types import SimpleNamespace
import numpy as np

from cloth_next.bake.controller import BakeController
from cloth_next.bake.status import BakeState


def fixture(blender_env, monkeypatch, tmp_path):
    from cloth_next.blender import water_preparation as module
    flow = sys.modules['cloth_next.blender.water_flow']
    controller = BakeController()
    monkeypatch.setattr(module, 'shared_controller', controller)
    monkeypatch.setattr(flow.bpy.path, 'abspath', lambda value: value)
    paths = []
    for i in range(4):
        p = tmp_path / f'source{i}'
        p.write_bytes(b'source')
        paths.append(p)
    samples = []
    class Provider:
        def __init__(self, context, domain, frames, *, hash_sources):
            assert not hash_sources
            self.minimum, self.maximum = [0,0,0], [1,1,1]
            self.identity = {'sources': [], 'mode': 'test'}
            self.source_paths = paths
        def sample(self, frame):
            samples.append((frame, threading.get_ident()))
            context.scene.frame_current = frame
            return np.asarray([[.5,.5,.5]]), np.asarray([[2.,0,0]])
    monkeypatch.setattr(flow, 'FLIPWaterFlowProvider', Provider)
    monkeypatch.setattr(flow, 'water_settings_record', lambda obj: {'path':obj.cloth_next.water_flow_container})
    s = SimpleNamespace(water_flow_container='', cache_directory=str(tmp_path / 'ClothCache'),
                        water_flow_domain=object(), water_flow_resolution='3')
    obj = SimpleNamespace(name='Cloth', cloth_next=s)
    scene = SimpleNamespace(frame_current=7, frame_subframe=.5, render=SimpleNamespace(fps=24,fps_base=1))
    scene.frame_set = lambda frame, **kw: setattr(scene, 'frame_current', frame)
    context = SimpleNamespace(scene=scene)
    return module, controller, context, obj, samples


def pump(module):
    deadline = time.monotonic()+5
    while module.active():
        assert time.monotonic() < deadline
        module._pump()
        time.sleep(.005)


def test_same_job_shares_verified_field_without_recapturing(blender_env,monkeypatch,tmp_path):
    module,c,context,obj,samples=fixture(blender_env,monkeypatch,tmp_path)
    second=SimpleNamespace(name='Second',cloth_next=SimpleNamespace(**vars(obj.cloth_next)))
    target=str(tmp_path/'shared.gaia')
    obj.cloth_next.water_flow_container=target
    second.cloth_next.water_flow_container=target
    calls=[]
    original=module.WaterPreparationWorker
    def worker(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'WaterPreparationWorker',worker)
    try:
        module.start(context,(obj,second),1,2,wait_for_window=False)
        pump(module)
        assert c.snapshot().state is BakeState.FINISHED
        assert len(calls)==1
        assert samples==[(1,threading.get_ident()),(2,threading.get_ident())]
    finally:module.shutdown()


def test_shared_proof_rechecks_changed_source(blender_env,monkeypatch,tmp_path):
    module,c,context,obj,samples=fixture(blender_env,monkeypatch,tmp_path)
    flow=sys.modules['cloth_next.blender.water_flow']
    original_provider=flow.FLIPWaterFlowProvider
    captures=[]
    class Provider(original_provider):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            captures.append(1)
            if len(captures)==2:self.source_paths[0].write_bytes(b'changed source')
    monkeypatch.setattr(flow,'FLIPWaterFlowProvider',Provider)
    target=str(tmp_path/'shared.gaia')
    obj.cloth_next.water_flow_container=target
    second=SimpleNamespace(name='Second',cloth_next=SimpleNamespace(**vars(obj.cloth_next)))
    try:
        module.start(context,(obj,second),1,2,wait_for_window=False);pump(module)
        assert c.snapshot().state is BakeState.FINISHED
        assert len(samples)==4
    finally:module.shutdown()


def test_incompatible_shared_destination_keeps_first_valid_cache(blender_env,monkeypatch,tmp_path):
    module,c,context,obj,samples=fixture(blender_env,monkeypatch,tmp_path)
    flow=sys.modules['cloth_next.blender.water_flow']
    monkeypatch.setattr(flow,'water_grid_plan',lambda provider,s:dict(
        dimensions=[int(s.water_flow_resolution)]*3,support_threshold=1.))
    target=str(tmp_path/'shared.gaia')
    obj.cloth_next.water_flow_container=target
    second=SimpleNamespace(name='Second',cloth_next=SimpleNamespace(**vars(obj.cloth_next)))
    second.cloth_next.water_flow_resolution='4'
    try:
        module.start(context,(obj,second),1,2,wait_for_window=False);pump(module)
        assert c.snapshot().state is BakeState.ERROR
        assert 'separate .gaia file' in c.snapshot().error_summary
        from cloth_next.gaia.container import GaiaContainer
        with GaiaContainer(target) as cache:assert cache.frame(0).velocity.shape==(3,3,3,3)
        assert len(samples)==2
    finally:module.shutdown()


def test_standalone_cache_not_rejected_by_sequence_upload_budget(blender_env,monkeypatch,tmp_path):
    module,c,context,obj,samples=fixture(blender_env,monkeypatch,tmp_path)
    from cloth_next.gaia import solver_fields
    monkeypatch.setattr(solver_fields,'MAX_UPLOAD_BYTES',1)
    try:
        module.start(context,(obj,),1,2,wait_for_window=False)
        pump(module)
        assert c.snapshot().state is BakeState.FINISHED
        assert len(samples)==2
    finally:module.shutdown()


def test_production_preparation_does_not_apply_whole_schedule_budget(blender_env,monkeypatch,tmp_path):
    module,c,context,obj,samples=fixture(blender_env,monkeypatch,tmp_path)
    from cloth_next.gaia import solver_fields
    monkeypatch.setattr(solver_fields,'MAX_UPLOAD_BYTES',1)
    try:
        job=c.transition(BakeState.PREPARING).job_id
        module.start(context,(obj,),1,2,job_id=job,wait_for_window=False)
        pump(module)
        state=c.snapshot()
        assert state.state is BakeState.FINISHED and len(samples)==2
    finally:module.shutdown()


def test_timer_capture_uses_main_thread_and_normal_cloth_folder(blender_env, monkeypatch, tmp_path):
    module, c, context, obj, samples = fixture(blender_env, monkeypatch, tmp_path)
    try:
        module.start(context, (obj,), 1, 2, wait_for_window=False)
        pump(module)
        assert samples == [(1,threading.get_ident()),(2,threading.get_ident())]
        assert c.snapshot().state is BakeState.FINISHED
        assert context.scene.frame_current == 7
        assert obj.cloth_next.water_flow_container == str(tmp_path/'ClothCache'/'Cloth_WaterFlow.gaia')
        assert (tmp_path/'ClothCache'/'Cloth_WaterFlow.gaia').is_file()
    finally:
        module.shutdown()


def test_cancelled_capture_restores_timeline_and_cleans_worker(blender_env, monkeypatch, tmp_path):
    module, c, context, obj, samples = fixture(blender_env, monkeypatch, tmp_path)
    try:
        job = module.start(context, (obj,), 1, 2, wait_for_window=False)
        module._pump()
        assert module.cancel(job)
        pump(module)
        assert c.snapshot().state is BakeState.CANCELLED
        assert context.scene.frame_current == 7
        assert not list(tmp_path.rglob('*.tmp'))
        assert not samples
    finally:
        module.shutdown()


def test_verified_container_is_available_to_same_bake_continuation(blender_env, monkeypatch, tmp_path):
    module, c, context, obj, samples = fixture(blender_env, monkeypatch, tmp_path)
    ready = []
    try:
        owner = c.transition(BakeState.PREPARING)
        def complete():
            ready.append(module.prepared_container(obj,1,2))
            c.transition(BakeState.PREPARING, job_id=owner.job_id)
        module.start(context, (obj,), 1, 2, job_id=owner.job_id,
                     on_complete=complete, wait_for_window=False)
        pump(module)
        assert ready[0].is_file()
        assert c.snapshot().job_id == owner.job_id
        assert c.snapshot().state is BakeState.PREPARING
    finally:
        module.shutdown()


def test_previous_prepare_reused_by_next_bake_with_individual_controls(blender_env,monkeypatch,tmp_path):
    module,c,context,obj,samples=fixture(blender_env,monkeypatch,tmp_path)
    flow=sys.modules['cloth_next.blender.water_flow']
    obj.cloth_next.water_flow_influence=1.
    obj.cloth_next.water_flow_velocity_scale=1.
    monkeypatch.setattr(flow,'water_settings_record',lambda item:dict(
        path=item.cloth_next.water_flow_container,
        influence=item.cloth_next.water_flow_influence,
        velocity_scale=item.cloth_next.water_flow_velocity_scale))
    calls=[]
    original=module.WaterPreparationWorker
    def worker(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'WaterPreparationWorker',worker)
    try:
        module.start(context,(obj,),1,2,wait_for_window=False);pump(module)
        obj.cloth_next.water_flow_influence=.25
        obj.cloth_next.water_flow_velocity_scale=.5
        c.reset();owner=c.transition(BakeState.PREPARING)
        ready=[]
        def complete():ready.append(module.prepared_container(obj,1,2))
        module.start(context,(obj,),1,2,job_id=owner.job_id,on_complete=complete,wait_for_window=False)
        pump(module)
        assert len(calls)==1 and len(samples)==2
        assert ready[0].is_file()
        assert module._prepared[(obj.name,1,2)]['job']==owner.job_id
    finally:module.shutdown()


def test_new_job_rechecks_source_and_cache_changes(blender_env,monkeypatch,tmp_path):
    module,c,context,obj,samples=fixture(blender_env,monkeypatch,tmp_path)
    calls=[];original=module.WaterPreparationWorker
    def worker(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'WaterPreparationWorker',worker)
    try:
        module.start(context,(obj,),1,2,wait_for_window=False);pump(module)
        assert len(calls)==1
        (tmp_path/'source0').write_bytes(b'changed')
        module.start(context,(obj,),1,2,wait_for_window=False);pump(module)
        assert len(calls)==2 and len(samples)==4
        cache=module.Path(obj.cloth_next.water_flow_container)
        cache.write_bytes(b'corrupted cache')
        module.start(context,(obj,),1,2,wait_for_window=False);pump(module)
        assert len(calls)==3
        assert c.snapshot().state is BakeState.ERROR
    finally:module.shutdown()


def test_new_job_may_rebuild_changed_grid_on_same_destination(blender_env,monkeypatch,tmp_path):
    module,c,context,obj,samples=fixture(blender_env,monkeypatch,tmp_path)
    flow=sys.modules['cloth_next.blender.water_flow']
    monkeypatch.setattr(flow,'water_grid_plan',lambda provider,s:dict(
        dimensions=[int(s.water_flow_resolution)]*3,support_threshold=1.))
    try:
        module.start(context,(obj,),1,2,wait_for_window=False);pump(module)
        obj.cloth_next.water_flow_resolution='4'
        module.start(context,(obj,),1,2,wait_for_window=False);pump(module)
        assert c.snapshot().state is BakeState.FINISHED
        from cloth_next.gaia.container import GaiaContainer
        with GaiaContainer(obj.cloth_next.water_flow_container) as cache:
            assert cache.frame(0).velocity.shape==(4,4,4,3)
        assert len(samples)==4
    finally:module.shutdown()
