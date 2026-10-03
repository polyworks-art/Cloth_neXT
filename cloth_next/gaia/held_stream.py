# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive official public held sessions without retaining temporal schedules.

Process ownership and durable recovery belong to the caller. The driver always
requests a checkpoint on cancellation or failure; it never releases stale input.
"""
from dataclasses import dataclass
import time


class WaterStreamCancelled(Exception):
    pass


@dataclass(frozen=True)
class StreamTarget:
    reader: object
    groups: tuple
    influence: float = 1.
    velocity_scale: float = 1.


def drive_held_stream(session, targets, field_factory, *, final_boundary,
                      resume_boundary=0, cancelled=lambda: False,
                      notify=lambda state: None, add_base_fields=lambda field: None,
                      hold_timeout=120., shutdown_timeout=30., start_held=None):
    """Update only while held; all field objects die before integration resumes.

    ``resume_boundary`` must come from an authenticated solver checkpoint, not a
    progress counter. A newly resumed process first holds at that boundary.
    ``notify`` hands boundary/source identity to existing recovery/progress code.
    No native process is launched or killed directly by this module.
    """
    if not targets or not 0 <= resume_boundary <= final_boundary:
        raise ValueError('Invalid Water Flow streaming targets or boundary')
    def check_cancel():
        if cancelled():
            raise WaterStreamCancelled('Water Flow streaming cancelled')
    def actual_boundary(minimum):
        deadline=time.monotonic()+hold_timeout
        while True:
            check_cancel()
            held=session.held_frame()
            if held is not None:
                if not isinstance(held,int) or not resume_boundary<=held<=final_boundary:
                    raise RuntimeError(f'GAIA reported an invalid Water Flow held frame: {held}')
                if held>=minimum:return held
            if not session.is_running():
                # save_and_quit also writes finished.txt. During resume that
                # old marker can make public run_until_frame return before
                # native startup has removed it. Only real output completion
                # or a new held marker is authoritative here.
                if session.finished() and session.get.latest_frame()>=final_boundary:
                    return final_boundary
                raise RuntimeError('GAIA exited before the requested Water Flow boundary')
            if time.monotonic()>=deadline:
                raise TimeoutError('GAIA did not confirm the requested Water Flow hold')
            time.sleep(.02)
    owns_run=False
    try:
        check_cancel()
        # A saved held marker is not a running session. Resume must launch the
        # authenticated checkpoint and confirm its new hold before uploading.
        running=session.is_running()
        if session.held_frame() is None or not running:
            if running:
                raise RuntimeError('GAIA reports another active simulation; finish or cancel it before Water Flow Bake')
            owns_run=True
            if start_held is None:
                session.run_until_frame(resume_boundary, timeout=hold_timeout)
            else:
                start_held(resume_boundary,hold_timeout)
        else:
            owns_run=True
        boundary = actual_boundary(resume_boundary)
        if boundary != resume_boundary:
            raise RuntimeError(f'GAIA held boundary {boundary} differs from authenticated resume boundary {resume_boundary}')
        while boundary < final_boundary:
            check_cancel()
            field = field_factory()
            try:
                add_base_fields(field)
                identities=[]
                for target in targets:
                    check_cancel()
                    window=target.reader.window(boundary, influence=target.influence,
                                                velocity_scale=target.velocity_scale)
                    identities.append({'cache_fingerprint':target.reader.fingerprint,
                        'source_frames':list(window.source_frames),
                        'times':list(window.times),
                        'timings':getattr(target.reader,'last_window_timings',{})})
                    field.grid(window.values, window.minimum, window.maximum,
                               times=window.times, kind='air-velocity',
                               groups=list(target.groups))
                    del window
                check_cancel()
                notify({'phase':'UPLOADING','boundary':boundary,'targets':identities})
                start=time.perf_counter()
                session.update_force_field(field)
                elapsed=time.perf_counter()-start
            finally:
                field.clear()
                del field
            notify({'phase':'ACTIVE','boundary':boundary,'targets':identities,
                    'upload_seconds':elapsed})
            check_cancel()
            start=time.perf_counter()
            session.run_until_frame(boundary+1, timeout=hold_timeout)
            new_boundary=actual_boundary(boundary+1)
            if new_boundary <= boundary:
                raise RuntimeError('GAIA did not advance the Water Flow boundary')
            boundary=new_boundary
            notify({'phase':'COMPLETED','boundary':boundary,
                    'step_seconds':time.perf_counter()-start})
        if not session.finished():
            raise RuntimeError('GAIA reached the final Water Flow boundary without finishing')
        return boundary
    except BaseException:
        # A held native run consumes this public flag without being released.
        # The owner must still reap the process tree if graceful saving stalls.
        if owns_run and session.is_running():
            # Cancellation can race the native completion of the requested
            # step before actual_boundary publishes it. Persist the confirmed
            # hold, not the previous progress counter, for existing Recovery.
            try:
                held=session.held_frame()
                if type(held) is int and resume_boundary<=held<=final_boundary:
                    notify({'phase':'CHECKPOINTING','boundary':held,
                            'last_completed_boundary':held})
            except Exception:
                pass  # A broken observer must not prevent native checkpointing.
            session.save_and_quit()
            deadline=time.monotonic()+shutdown_timeout
            while session.is_running() and time.monotonic()<deadline:
                time.sleep(.02)
        raise
