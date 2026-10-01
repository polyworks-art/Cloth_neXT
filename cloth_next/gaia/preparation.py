# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded asynchronous field conversion. This worker never imports Blender."""
import hashlib
import json
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

from .container import GaiaContainer, write_water_container
from .water_field import fingerprint, reconstruct


class PreparationCancelled(Exception):
    pass


def source_stamp(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class WaterPreparationWorker:
    """One requested sample and one response; at most one particle frame resident."""
    def __init__(self, path, metadata, source_paths, frames, minimum, maximum):
        self.path = Path(path)
        self.metadata = json.loads(json.dumps(metadata))
        self.paths = tuple(Path(p) for p in source_paths)
        self.frames = tuple(frames)
        self.minimum, self.maximum = tuple(minimum), tuple(maximum)
        self.requests, self.responses = Queue(maxsize=1), Queue(maxsize=1)
        self.events = Queue()
        self.cancelled, self.done = Event(), Event()
        self.error = None
        self.result = None
        self.source_stamps = []
        self.thread = Thread(target=self._run, name='ClothNeXtWaterField', daemon=True)

    def check_cancel(self):
        if self.cancelled.is_set(): raise PreparationCancelled()

    def progress(self, stage, current, total, frame=None):
        overall = current if stage == 'Checking FLIP bake' else len(self.paths)+current
        self.events.put((stage, current, total, frame, overall))

    def _hash_sources(self):
        sources = []
        for i, path in enumerate(self.paths):
            self.check_cancel()
            before = source_stamp(path)
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    self.check_cancel()
                    digest.update(chunk)
            if source_stamp(path) != before:
                raise ValueError('FLIP bake changed during Water Flow preparation; finish baking and retry')
            self.source_stamps.append((path, before))
            sources.append([path.name, digest.hexdigest()])
            self.progress('Checking FLIP bake', i+1, len(self.paths))
        self.metadata['provider']['sources'] = sources
        self.metadata['fingerprint'] = fingerprint(self.metadata)

    def _check_sources(self):
        self.check_cancel()
        if any(source_stamp(path) != stamp for path, stamp in self.source_stamps):
            raise ValueError('FLIP bake changed during Water Flow preparation; retry after baking finishes')

    def _sequence(self):
        for i, frame in enumerate(self.frames):
            self.check_cancel()
            self.requests.put(frame)
            while True:
                self.check_cancel()
                try:
                    sample = self.responses.get(timeout=.05)
                    break
                except Empty:
                    continue
            if isinstance(sample, Exception): raise sample
            self.events.put(('Reconstructing flow', i, len(self.frames), frame, len(self.paths)+i))
            positions, velocities = sample
            field = reconstruct(positions, velocities, self.minimum, self.maximum,
                                self.metadata['dimensions'])
            del sample, positions, velocities
            self.check_cancel()
            yield frame, (frame-self.frames[0])/self.metadata['fps'], field
            self.progress('Reconstructing flow', i+1, len(self.frames), frame)
        self._check_sources()

    def _run(self):
        try:
            self._hash_sources()
            if self.path.is_file():
                with GaiaContainer(self.path) as cached:
                    if cached.manifest['water']['fingerprint'] == self.metadata['fingerprint']:
                        for i in range(len(cached.manifest['frames'])):
                            self.check_cancel()
                            cached.frame(i)
                            self.progress('Checking water cache', i+1, len(self.frames))
                        self._check_sources()
                        self.result = self.path
                        return
            write_water_container(self.path, self.metadata, self._sequence(), check_cancel=self.check_cancel)
            self.result = self.path
        except Exception as exc:
            self.error = exc
        finally:
            self.done.set()

    def start(self):
        self.thread.start()
        return self

    def cancel(self):
        self.cancelled.set()
