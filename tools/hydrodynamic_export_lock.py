"""Serialize the official frontend's shared session-link bookkeeping only."""
from contextlib import contextmanager
from pathlib import Path
import time


@contextmanager
def export_lock():
    import msvcrt
    path=Path(__file__).resolve().parents[1]/'docs/validation/gaia026/hydrodynamics/frontend-export.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as lock:
        if path.stat().st_size==0:lock.write(b'0');lock.flush()
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError:time.sleep(.1)
        try:yield
        finally:
            lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
