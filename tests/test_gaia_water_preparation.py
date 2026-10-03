from queue import Empty
import threading
import time
import numpy as np

from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.preparation import PreparationCancelled, WaterPreparationWorker


def worker(tmp_path, frames=(1, 2)):
    sources = []
    for frame in frames:
        for kind in ('positions', 'velocity'):
            path = tmp_path / f'{kind}{frame}'
            path.write_bytes(kind.encode())
            sources.append(path)
    metadata = {'provider': {'sources': [], 'mode': 'test'},
                'frames': [frames[0], frames[-1]], 'fps': 24., 'dimensions': [3, 3, 3]}
    return WaterPreparationWorker(tmp_path / 'water.gaia', metadata, sources, frames,
                                  [0, 0, 0], [1, 1, 1])


def feed(job):
    deadline = time.monotonic()+5
    requests = []
    while not job.done.is_set():
        assert time.monotonic() < deadline
        try: frame = job.requests.get(timeout=.02)
        except Empty: continue
        requests.append(frame)
        job.responses.put((np.asarray([[.5,.5,.5]]), np.asarray([[2.,0,0]])))
    job.thread.join(timeout=1)
    return requests


def test_worker_converts_bounded_samples_and_reuses_verified_cache(tmp_path, monkeypatch):
    from cloth_next.gaia import preparation
    threads = []
    original = preparation.reconstruct
    monkeypatch.setattr(preparation, 'reconstruct', lambda *args,**kwargs: (
        threads.append(threading.get_ident()) or original(*args,**kwargs)))
    job = worker(tmp_path).start()
    assert feed(job) == [1, 2]
    assert job.error is None
    assert threads and all(t != threading.get_ident() for t in threads)
    with GaiaContainer(job.result) as cache:
        assert len(cache.manifest['frames']) == 2
        assert len(cache.manifest['water']['provider']['sources']) == 4
    before = job.path.read_bytes()
    reused = worker(tmp_path).start()
    assert feed(reused) == []
    assert reused.error is None
    assert reused.path.read_bytes() == before


def test_cancel_while_waiting_for_capture_preserves_existing_cache(tmp_path):
    complete = worker(tmp_path).start()
    feed(complete)
    before = complete.path.read_bytes()
    cancelled = worker(tmp_path, frames=(1, 2, 3)).start()
    assert cancelled.requests.get(timeout=5) == 1
    cancelled.cancel()
    cancelled.thread.join(timeout=2)
    assert cancelled.done.is_set()
    assert isinstance(cancelled.error, PreparationCancelled)
    assert cancelled.path.read_bytes() == before
    assert not list(tmp_path.glob('*.tmp'))


def test_capture_failure_is_returned_and_partial_output_removed(tmp_path):
    job = worker(tmp_path).start()
    assert job.requests.get(timeout=5) == 1
    job.responses.put(ValueError('Velocity disappeared'))
    job.thread.join(timeout=2)
    assert isinstance(job.error, ValueError)
    assert str(job.error) == 'Velocity disappeared'
    assert not job.path.exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_cancellation_before_commit_keeps_old_file(tmp_path):
    from cloth_next.gaia.container import write_water_container
    from cloth_next.gaia.water_field import reconstruct
    path = tmp_path / 'old.gaia'
    path.write_bytes(b'old cache')
    field = reconstruct([[.5,.5,.5]], [[1,0,0]], [0,0,0], [1,1,1], [3,3,3])
    calls = []
    def check():
        calls.append(True)
        if len(calls) == 2: raise PreparationCancelled()
    try:
        write_water_container(path, {}, [(1,0,field)], check_cancel=check)
    except PreparationCancelled:
        pass
    assert path.read_bytes() == b'old cache'
    assert not list(tmp_path.glob('*.tmp'))


def test_previous_field_released_before_next_reconstruction(tmp_path,monkeypatch):
    import weakref
    from cloth_next.gaia import preparation
    original=preparation.reconstruct
    previous=[]
    def checked(*args,**kwargs):
        if previous:assert previous[-1]() is None, 'retained previous high-resolution frame'
        field=original(*args,**kwargs);previous.append(weakref.ref(field));return field
    monkeypatch.setattr(preparation,'reconstruct',checked)
    job=worker(tmp_path,frames=(1,2,3)).start()
    assert feed(job)==[1,2,3] and job.error is None
    assert previous[-1]() is None


def test_new_grid_recipe_invalidates_old_cache(tmp_path):
    complete=worker(tmp_path).start();feed(complete)
    changed=worker(tmp_path)
    changed.metadata['grid_layout']={'recipe':'isotropic-longest-axis-v2','dimensions':[3,3,3]}
    changed.start()
    assert feed(changed)==[1,2] and changed.error is None
    with GaiaContainer(changed.path) as cache:
        assert cache.manifest['water']['grid_layout']['recipe']=='isotropic-longest-axis-v2'
