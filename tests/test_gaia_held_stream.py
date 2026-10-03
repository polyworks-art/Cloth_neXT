# SPDX-License-Identifier: GPL-3.0-or-later
import weakref
from types import SimpleNamespace
import numpy as np
import pytest
from cloth_next.gaia.held_stream import StreamTarget, drive_held_stream, WaterStreamCancelled


class Session:
    def __init__(self, final=4, jumps=1):
        self.final=final;self.jumps=jumps;self.held=None;self.running=False
        self.calls=[];self.saved=False;self.refs=[]
    def held_frame(self):return self.held
    def finished(self):return self.held==self.final
    def is_running(self):return self.running
    def run_until_frame(self, boundary, **options):
        assert all(ref() is None for ref in self.refs)
        self.calls.append(('hold',boundary));self.running=True
        self.held=boundary if self.held is None else min(self.final,self.held+self.jumps)
        if self.finished():self.running=False
    def update_force_field(self, field):
        assert self.running and self.held is not None
        self.calls.append(('upload',self.held));self.refs.extend(weakref.ref(v) for v in field.values)
    def save_and_quit(self):self.saved=True;self.running=False


class Field:
    def __init__(self):self.values=[]
    def grid(self, values, *args, **kwargs):
        assert kwargs['kind']=='air-velocity';self.values.append(values)
    def clear(self):self.values.clear()


class Reader:
    fingerprint='cache-identity'
    def __init__(self):self.reads=[]
    def window(self,boundary,**options):
        self.reads.append(boundary)
        return SimpleNamespace(values=np.zeros((2,2,2,2,3),dtype='f4'),
            minimum=(0,0,0),maximum=(1,1,1),source_frames=(120+boundary,121+boundary),
            times=(boundary/24,(boundary+1)/24))


@pytest.mark.parametrize('jumps',[1,2])
def test_public_hold_order_actual_boundary_and_release(jumps):
    session=Session(jumps=jumps);reader=Reader();events=[]
    assert drive_held_stream(session,[StreamTarget(reader,('water',))],Field,
                            final_boundary=4,notify=events.append)==4
    assert reader.reads==list(range(0,4,jumps))
    assert session.calls[0]==('hold',0)
    assert all(ref() is None for ref in session.refs)
    assert not session.saved
    assert [e['boundary'] for e in events if e['phase']=='COMPLETED']==list(range(jumps,5,jumps))


def test_resume_uses_authenticated_boundary():
    session=Session();reader=Reader()
    drive_held_stream(session,[StreamTarget(reader,('water',))],Field,
                      final_boundary=4,resume_boundary=2)
    assert session.calls[0]==('hold',2) and reader.reads==[2,3]


def test_other_official_run_is_not_adopted_or_cancelled():
    session=Session();session.running=True;reader=Reader()
    with pytest.raises(RuntimeError,match='another active simulation'):
        drive_held_stream(session,[StreamTarget(reader,('water',))],Field,final_boundary=4)
    assert session.calls==[] and not session.saved and reader.reads==[]


def test_owned_native_resume_callback_holds_before_first_upload():
    session=Session();reader=Reader();calls=[]
    def start(boundary,timeout):
        calls.append(boundary);session.running=True;session.held=boundary
    drive_held_stream(session,[StreamTarget(reader,('water',))],Field,
                      final_boundary=4,resume_boundary=2,start_held=start)
    assert calls==[2] and reader.reads==[2,3]
    assert session.calls[0]==('upload',2)


def test_stale_save_completion_marker_waits_for_real_resume_hold(monkeypatch):
    class Resume(Session):
        pending=0
        def run_until_frame(self,boundary,**options):
            if not self.calls:
                self.calls.append(('hold',boundary));self.running=True;self.pending=boundary
            else:super().run_until_frame(boundary,**options)
        def held_frame(self):
            if self.pending:
                result=self.held;self.held=self.pending;self.pending=0
                return result
            return self.held
        def finished(self):return self.held is None or super().finished()
    monkeypatch.setattr('cloth_next.gaia.held_stream.time.sleep',lambda delay:None)
    session=Resume();reader=Reader()
    drive_held_stream(session,[StreamTarget(reader,('water',))],Field,
                      final_boundary=4,resume_boundary=2)
    assert reader.reads==[2,3]


def test_cancel_while_held_saves_without_releasing_stale_field():
    session=Session();reader=Reader();cancel=[False]
    def notify(event):
        if event['phase']=='ACTIVE':cancel[0]=True
    with pytest.raises(WaterStreamCancelled):
        drive_held_stream(session,[StreamTarget(reader,('water',))],Field,
                          final_boundary=4,cancelled=lambda:cancel[0],notify=notify)
    assert session.saved and session.held==0 and reader.reads==[0]
    assert all(ref() is None for ref in session.refs)


@pytest.mark.parametrize('failure',['read','upload','notify'])
def test_failure_while_held_requests_checkpoint(failure):
    session=Session();reader=Reader()
    def fail(*args,**kwargs):raise ValueError('broken input')
    notify=lambda event:None
    if failure=='read':reader.window=fail
    if failure=='upload':session.update_force_field=fail
    if failure=='notify':notify=fail
    with pytest.raises(ValueError,match='broken input'):
        drive_held_stream(session,[StreamTarget(reader,('water',))],Field,
                          final_boundary=4,notify=notify)
    assert session.saved and session.held==0


def test_stale_held_checkpoint_marker_starts_owned_resume():
    session=Session();session.held=2;reader=Reader();calls=[]
    def start(boundary,timeout):
        calls.append(boundary);session.running=True;session.held=boundary
    drive_held_stream(session,[StreamTarget(reader,('water',))],Field,
                      final_boundary=4,resume_boundary=2,start_held=start)
    assert calls==[2] and reader.reads==[2,3]


def test_cancel_racing_completed_step_records_actual_hold():
    session=Session();reader=Reader();events=[]
    def cancelled():
        return session.held is not None and session.held>=1
    with pytest.raises(WaterStreamCancelled):
        drive_held_stream(session,[StreamTarget(reader,('water',))],Field,
                          final_boundary=4,cancelled=cancelled,notify=events.append)
    assert session.saved and session.held==1
    assert events[-1]=={'phase':'CHECKPOINTING','boundary':1,'last_completed_boundary':1}
