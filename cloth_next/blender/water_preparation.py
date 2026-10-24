# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread FLIP capture pump feeding the Blender-free water worker."""
import json
import os
from pathlib import Path
from queue import Empty
import time
import traceback
import bpy

from ..bake.controller import shared_controller
from ..bake.status import BakeActivity, BakeJobKind, BakeState
from ..bake.transport import EnterBakeMode
from ..gaia.preparation import PreparationCancelled, WaterPreparationWorker, source_stamp
from ..gaia.water_field import fingerprint
from ..ppf_run.session import SessionCancelled
from . import companion_manager, modal_lock

_job = None
_prepared = {}


def _field_signature(metadata):
    document=dict(metadata)
    document.pop('fingerprint',None)
    document['provider']=dict(document['provider'],sources=[])
    return fingerprint(document)


PROOF_SUFFIX = '.prepared.json'


def _proof_path(container):
    container = Path(container)
    return container.with_name(container.name + PROOF_SUFFIX)


def _write_proof(proof):
    """Persist a verified preparation next to its .gaia file.

    It lets a later Blender session reuse the container when neither the
    Fluid Cache settings nor any FLIP source file changed (all file stamps
    are rechecked before reuse). Failure to write only costs a rebuild.
    """
    document = {'version': 1, 'path': str(proof['path']),
                'field_signature': proof['field_signature'],
                'cache_stamp': list(proof['cache_stamp']),
                'sources': [[str(path), list(stamp)] for path, stamp in proof['sources']]}
    target = _proof_path(proof['path'])
    temporary = target.with_name(target.name + '.tmp')
    try:
        temporary.write_text(json.dumps(document, sort_keys=True), encoding='utf-8')
        os.replace(temporary, target)
    except OSError:
        try: temporary.unlink()
        except OSError: pass


def _stored_proof(container):
    """The persisted proof for ``container``, or None when absent or damaged."""
    try:
        document = json.loads(_proof_path(container).read_text(encoding='utf-8'))
        if document.get('version') != 1 or Path(document['path']) != Path(container):
            return None
        return {'job': None, 'settings': None, 'path': Path(document['path']),
                'field_signature': document['field_signature'],
                'cache_stamp': tuple(document['cache_stamp']),
                'sources': tuple((Path(path), tuple(stamp))
                                 for path, stamp in document['sources'])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pressure_source_end(objects, first, last):
    """Last FLIP frame Hydrodynamic Pressure reads for a Bake ending at ``last``.

    The native solver may evaluate slightly past the final output time, so a
    genuine FLIP sample after the Bake end is used when it exists; otherwise
    the last FLIP frame is held for that final interval.
    """
    end = int(last) + 1
    for obj in objects:
        domain = getattr(obj.cloth_next, 'water_flow_domain', None)
        flip = getattr(getattr(domain, 'flip_fluid', None), 'domain', None)
        if flip is None:
            continue
        root = Path(flip.cache.get_cache_abspath()) / 'bakefiles'
        missing = [frame for frame in range(int(first), int(last) + 1)
                   if not (root / f'{frame:06d}.bobj').is_file()]
        if missing:
            raise ValueError(
                f'Hydrodynamic Pressure needs FLIP surface meshes for the Bake frames {first}-{last}; '
                f'FLIP surface {missing[0]:06d}.bobj is not baked. Bake FLIP over the Cloth Bake range.')
        if not (root / f'{end:06d}.bobj').is_file():
            end = int(last)
    return end


def active(job_id=None):
    return _job is not None and (job_id is None or _job['id'] == job_id)


def prepared_container(obj, first, last):
    """Reuse worker verification only inside its same, still-active Bake job."""
    from .water_flow import water_settings_record
    owner = shared_controller.snapshot()
    proof = _prepared.get((obj.name, int(first), int(last)))
    if (proof is None or not owner.active or proof['job'] != owner.job_id
            or proof['settings'] != water_settings_record(obj)):
        return None
    try:
        if source_stamp(proof['path']) != proof['cache_stamp']:
            return None
        if any(source_stamp(path) != stamp for path, stamp in proof['sources']):
            return None
    except OSError:
        return None
    return proof['path']


def start(context, objects, first, last, *, job_id=None, on_complete=None, wait_for_window=True):
    global _job
    from .water_flow import water_container_destination
    if active(): raise ValueError('Water Flow preparation is already running')
    standalone = job_id is None
    if standalone:
        if shared_controller.snapshot().active:
            raise ValueError('A Cloth NeXt Bake is already running')
        if shared_controller.snapshot().state is not BakeState.IDLE:
            shared_controller.reset()
        job_id = shared_controller.transition(BakeState.PREPARING,
            job_kind=BakeJobKind.WATER_FIELD, status_message='Preparing Water Flow').job_id
        if not modal_lock.reserve(job_id):
            shared_controller.fail('Another Cloth NeXt Bake owns the window')
            raise ValueError('Another Cloth NeXt Bake owns the window')
    if shared_controller.snapshot().job_id != job_id:
        raise ValueError('Water Flow preparation belongs to a stale Bake')
    try:
        if first > last: raise ValueError('Water Flow frame range is empty')
        objects = tuple(objects)
        if not objects: raise ValueError('No Water Flow Cloth object selected')
        if any(getattr(obj.cloth_next,'water_hydrodynamic_pressure',False) for obj in objects):
            last=pressure_source_end(objects,first,last)
        for obj in objects:
            s = obj.cloth_next
            if not s.water_flow_resolution:
                s.water_flow_resolution='AUTO'  # obsolete saved Low enum value
            value = s.water_flow_container
            if not value:
                value = getattr(s, 'cache_directory', '')
                if value and not value.endswith(('/', '\\')): value += os.sep
            if not value:
                value = f'//{bpy.path.clean_name(obj.name)}_WaterFlow.gaia'
            s.water_flow_container = str(water_container_destination(value, obj.name))
        if standalone and wait_for_window:
            shared_controller.transition(BakeState.STARTING_COMPANION)
            request = EnterBakeMode(job_id=job_id, blender_process_id=os.getpid(),
                frame_start=first, frame_end=last, preset_label='GAIA Water Flow')
            ok, message = companion_manager.begin_bake_mode(request)
            if not ok: raise ValueError(message)
            shared_controller.transition(BakeState.WAITING_FOR_COMPANION,
                status_message='Opening Water Flow preparation window')
        elif not wait_for_window:
            shared_controller.transition(BakeState.PREPARING_WATER)
        _job = {'id':job_id, 'context':context, 'objects':objects, 'index':0,
                'first':int(first), 'last':int(last), 'worker':None, 'provider':None,
                'original':context.scene.frame_current,
                'subframe':float(getattr(context.scene, 'frame_subframe', 0)),
                'started':time.monotonic(), 'deadline':time.monotonic()+companion_manager.STARTUP_TIMEOUT_SECONDS,
                'waiting':bool(wait_for_window), 'standalone':standalone,
                'complete':on_complete, 'error':None}
        if bpy.app.timers.is_registered(_pump): bpy.app.timers.unregister(_pump)
        bpy.app.timers.register(_pump, first_interval=.05)
        return job_id
    except Exception:
        if standalone:
            modal_lock.release(job_id)
            shared_controller.fail('Water Flow preparation could not start', traceback.format_exc())
        raise


def cancel(job_id=None):
    if not active(job_id): return False
    if _job['worker'] is not None: _job['worker'].cancel()
    owner = shared_controller.snapshot()
    if owner.job_id == _job['id'] and owner.active and owner.state is not BakeState.CANCELLING:
        shared_controller.request_cancel()
    return True


def _restore(state):
    try:
        state['context'].scene.frame_set(state['original'], subframe=state['subframe'])
    except (ReferenceError, RuntimeError, AttributeError):
        pass


def _finish(state, *, cancelled=False, error=None):
    global _job
    _restore(state)
    _job = None
    owner = shared_controller.snapshot()
    if owner.job_id != state['id']:
        modal_lock.release(state['id'])
        return None
    if cancelled:
        companion_manager.cancel_startup(state['id'], 'Water Flow preparation cancelled')
        if owner.state is not BakeState.CANCELLING: shared_controller.request_cancel()
        shared_controller.transition(BakeState.CANCELLED,
            status_title='Water Flow cancelled', status_message='Water Flow preparation stopped',
            activity_label='Water Flow cancelled')
        modal_lock.release(state['id'])
    elif error is not None:
        shared_controller.fail(str(error), ''.join(traceback.format_exception(error)))
        companion_manager.persist_bake_error(shared_controller.snapshot())
        modal_lock.release(state['id'])
    elif state['complete'] is not None:
        try:
            state['complete']()
        except SessionCancelled:
            owner = shared_controller.snapshot()
            if owner.job_id == state['id'] and owner.state is not BakeState.CANCELLED:
                if owner.state is not BakeState.CANCELLING:
                    shared_controller.request_cancel()
                shared_controller.transition(BakeState.CANCELLED,
                    status_message='Bake preparation cancelled')
            modal_lock.release(state['id'])
        except Exception as exc:
            if shared_controller.snapshot().state is not BakeState.ERROR:
                shared_controller.fail(str(exc), traceback.format_exc())
            modal_lock.release(state['id'])
    else:
        shared_controller.transition(BakeState.FINISHED, status_title='Water Flow ready',
            status_message='.gaia water field prepared', activity_label='Water Flow ready',
            progress_current=shared_controller.snapshot().progress_total or 0)
        modal_lock.release(state['id'])
    return None


def _pump():
    from .water_flow import FLIPWaterFlowProvider, water_settings_record, water_grid_plan
    state = _job
    if state is None: return None
    worker = state['worker']
    owner = shared_controller.snapshot()
    cancelled = owner.job_id != state['id'] or owner.state in {BakeState.CANCELLING, BakeState.CANCELLED}
    if cancelled or state['error'] is not None:
        if worker is not None:
            worker.cancel()
            if not worker.done.is_set(): return .05
        return _finish(state, cancelled=cancelled, error=state['error'])
    try:
        if state['waiting']:
            status, message = (companion_manager.startup_status(state['id']) if state['standalone']
                               else companion_manager.preparation_status())
            if status != 'READY':
                if status == 'CANCELLED': return _finish(state, cancelled=True)
                if status in {'ERROR', 'CANCELLED'} or time.monotonic() >= state['deadline']:
                    raise ValueError(message)
                shared_controller.update(status_message=message)
                return .05
            if state['standalone']:
                if not companion_manager.consume_ready(state['id']): return .05
                shared_controller.transition(BakeState.COMPANION_READY)
                shared_controller.transition(BakeState.STARTING_RUN)
            shared_controller.transition(BakeState.PREPARING_WATER)
            state['waiting'] = False
        if worker is None:
            obj = state['objects'][state['index']]
            frames = range(state['first'], state['last']+1)
            provider = FLIPWaterFlowProvider(state['context'], obj.cloth_next.water_flow_domain,
                                             frames, hash_sources=False)
            layout = water_grid_plan(provider,obj.cloth_next)
            dims = layout['dimensions']
            # Prepared animation stays on disk. Runtime validates its bounded
            # two-frame held window instead of uploading a temporal schedule.
            fps = state['context'].scene.render.fps/state['context'].scene.render.fps_base
            metadata = {'provider':provider.identity, 'frames':[state['first'],state['last']], 'fps':fps,
                        'dimensions':dims, 'algorithm':'trilinear-splat-support-v2',
                        'field_format':'velocity-occupancy-v1', 'physical_velocity_units':'m/s',
                        'velocity_policy':'unclamped', 'grid_layout':layout}
            state['provider'] = provider
            state['settings'] = water_settings_record(obj)
            state['field_signature']=_field_signature(metadata)
            destination=Path(obj.cloth_next.water_flow_container)
            stored=_stored_proof(destination)
            for proof in tuple(_prepared.values())+((stored,) if stored else ()):
                if proof['path']!=destination:continue
                if proof.get('field_signature')!=state['field_signature']:
                    if proof['job']==state['id']:
                        raise ValueError('These Cloth objects use different Fluid Cache settings but the same .gaia file. '
                                         'Choose a separate .gaia file for each fluid source or grid.')
                    continue
                if tuple(path for path,stamp in proof['sources'])!=tuple(map(Path,provider.source_paths)):
                    continue
                try:
                    unchanged=(source_stamp(destination)==proof['cache_stamp'] and
                        all(source_stamp(path)==stamp for path,stamp in proof['sources']))
                except OSError:unchanged=False
                if not unchanged:continue
                # A prior Prepare or Bake in this Blender session already
                # checked every source and cached frame. Recheck all file
                # stamps and field settings, then bind the proof to this job.
                _prepared[(obj.name,state['first'],state['last'])]=dict(proof,job=state['id'],settings=state['settings'])
                state['index']+=1;state['provider']=None
                shared_controller.update(status_message='Using verified Fluid Cache',
                    activity_label='Using verified Fluid Cache',active_object_name=obj.name,
                    progress_current=state['index']*1000,progress_total=len(state['objects'])*1000,
                    preparation_current=len(frames),preparation_total=len(frames))
                if state['index']==len(state['objects']):return _finish(state)
                return .05
            worker = WaterPreparationWorker(Path(obj.cloth_next.water_flow_container), metadata,
                provider.source_paths, frames, provider.minimum, provider.maximum).start()
            state['worker'] = worker
        progress = None
        while True:
            try: progress = worker.events.get_nowait()
            except Empty: break
        if progress is not None:
            stage, current, total, frame, overall = progress
            shared_controller.update(status_title='Preparing Water Flow',
                status_message=stage, activity_code=BakeActivity.PREPARING_WATER,
                activity_label=f'{stage} · {current} / {total}', current_frame=frame,
                progress_current=int(1000*(state['index']+min(1.,overall/(len(worker.paths)+len(worker.frames))))),
                progress_total=len(state['objects'])*1000,
                preparation_current=current, preparation_total=total,
                active_object_name=state['objects'][state['index']].name,
                elapsed_seconds=time.monotonic()-state['started'])
        if worker.done.is_set():
            if worker.error is not None:
                return _finish(state, cancelled=isinstance(worker.error, PreparationCancelled), error=worker.error)
            obj = state['objects'][state['index']]
            if water_settings_record(obj) != state['settings']:
                raise ValueError('Water Flow settings changed during preparation; retry')
            _prepared[(obj.name,state['first'],state['last'])] = {
                'job':state['id'], 'settings':state['settings'], 'path':worker.result,
                'field_signature':state['field_signature'],
                'cache_stamp':source_stamp(worker.result), 'sources':tuple(worker.source_stamps)}
            _write_proof(_prepared[(obj.name,state['first'],state['last'])])
            state['index'] += 1
            state['worker'] = None
            state['provider'] = None
            if state['index'] == len(state['objects']): return _finish(state)
            return .05
        try: frame = worker.requests.get_nowait()
        except Empty: frame = None
        if frame is not None:
            shared_controller.update(activity_label=f'Reading FLIP · Frame {frame}', current_frame=frame)
            try:
                provider=state['provider']
                sample=getattr(provider,'sample_stream',provider.sample)(frame)
            except Exception as exc: sample = exc
            worker.responses.put_nowait(sample)
        return .05
    except Exception as exc:
        state['error'] = exc
        if worker is not None and not worker.done.is_set():
            worker.cancel()
            return .05
        return _finish(state, error=exc)


def shutdown():
    global _job
    if _job is not None:
        state = _job
        cancel(state['id'])
        if state['worker'] is not None:
            state['worker'].thread.join(timeout=2)
        _restore(state)
        modal_lock.release(state['id'])
        _job = None
    if bpy.app.timers.is_registered(_pump): bpy.app.timers.unregister(_pump)
    _prepared.clear()
