# SPDX-License-Identifier: GPL-3.0-or-later
import os
import threading
import pytest
from cloth_next.core.atomic_text import atomic_text_reader,read_atomic_text,write_atomic_text


def test_reader_keeps_old_snapshot_while_writer_publishes_successor(tmp_path):
    path=tmp_path/'status.json'
    path.write_text('{"boundary":1}',encoding='utf-8')
    started=threading.Event();errors=[]
    def publish():
        started.set()
        try:write_atomic_text(path,'{"boundary":2}')
        except BaseException as exc:errors.append(exc)
    with atomic_text_reader(path) as reader:
        writer=threading.Thread(target=publish)
        writer.start();assert started.wait(2)
        assert reader.read()=='{"boundary":1}'
    writer.join(2)
    assert not writer.is_alive() and not errors
    assert read_atomic_text(path)=='{"boundary":2}'
    # Closing the reader releases the last handle to its old snapshot, and
    # the published generation is immediately movable and deletable.
    moved=tmp_path/'moved.json'
    os.replace(path,moved)
    moved.unlink()


def test_missing_snapshot_does_not_create_or_adopt_a_file(tmp_path):
    path=tmp_path/'missing.json'
    with pytest.raises(FileNotFoundError):read_atomic_text(path)
    assert not path.exists()


def test_repeated_concurrent_snapshots_never_block_publication(tmp_path):
    path=tmp_path/'status.json'
    write_atomic_text(path,'0')
    errors=[];done=threading.Event();seen=[]
    def publish():
        try:
            for boundary in range(1,251):write_atomic_text(path,str(boundary))
        except BaseException as exc:errors.append(exc)
        finally:done.set()
    writer=threading.Thread(target=publish)
    writer.start()
    while not done.is_set():seen.append(int(read_atomic_text(path)))
    writer.join(2)
    assert not writer.is_alive() and not errors
    assert read_atomic_text(path)=='250'
    assert all(a<=b for a,b in zip(seen,seen[1:]))


def test_windows_transient_replace_is_bounded_and_keeps_atomic_operation(monkeypatch):
    from types import SimpleNamespace
    from cloth_next.core import atomic_text
    attempts=[];clock=[0.]
    def replace(source,destination):
        attempts.append((source,destination))
        if len(attempts)<3:
            error=PermissionError('temporary scanner handle');error.winerror=32
            raise error
    monkeypatch.setattr(atomic_text,'os',SimpleNamespace(name='nt',replace=replace))
    monkeypatch.setattr(atomic_text,'time',SimpleNamespace(monotonic=lambda:clock[0],
        sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds)))
    atomic_text._replace_snapshot('new','old')
    assert attempts==[('new','old')]*3 and clock[0]<.25


def test_permanent_windows_replace_error_is_not_hidden(monkeypatch):
    from types import SimpleNamespace
    from cloth_next.core import atomic_text
    clock=[0.]
    error=PermissionError('persistent denial');error.winerror=5
    def replace(*args):raise error
    monkeypatch.setattr(atomic_text,'os',SimpleNamespace(name='nt',replace=replace))
    monkeypatch.setattr(atomic_text,'time',SimpleNamespace(monotonic=lambda:clock[0],
        sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds)))
    with pytest.raises(PermissionError,match='persistent denial'):
        atomic_text._replace_snapshot('new','old')
    assert clock[0]==pytest.approx(.25)
