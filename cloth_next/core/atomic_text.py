# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic text snapshots with bounded Windows publisher/reader coordination.

Windows MoveFileEx replacement can reject an open destination even when the
reader shares deletion. Cooperating readers close before publication through
one OS mutex. Short Windows filesystem/scanner holds get a bounded atomic
replace retry; permanent failures still surface without changing permissions.
POSIX replacement already preserves an open reader's old snapshot.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import uuid
import time

from .filesystem_paths import io_path, resolved_path


@contextmanager
def _publication_lock(path, *, timeout_seconds=5.):
    if os.name != 'nt':
        yield
        return
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=(ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR)
    kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=(wintypes.HANDLE,wintypes.DWORD)
    kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.ReleaseMutex.argtypes=(wintypes.HANDLE,)
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    # Resolve only the parent: resolving the destination itself briefly
    # opens it on Windows BEFORE the mutex, recreating the replacement race.
    path=Path(path)
    identity=str(resolved_path(path.parent)/path.name).casefold().encode('utf-8')
    name='Local\\ClothNeXtAtomicText_'+hashlib.sha256(identity).hexdigest()
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    acquired=False
    try:
        outcome=kernel.WaitForSingleObject(handle,int(timeout_seconds*1000))
        if outcome==258:raise TimeoutError(f'Atomic text snapshot is busy: {path}')
        if outcome not in (0,128):raise ctypes.WinError(ctypes.get_last_error())
        acquired=True
        yield
    finally:
        if acquired:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


@contextmanager
def atomic_text_reader(path, *, encoding='utf-8'):
    with _publication_lock(path):
        with io_path(Path(path)).open('r',encoding=encoding) as stream:
            yield stream


def read_atomic_text(path, *, encoding='utf-8'):
    with atomic_text_reader(path,encoding=encoding) as stream:
        return stream.read()


def _replace_snapshot(source, destination):
    deadline = time.monotonic() + .25
    delay = .001
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            # Also occurs without readers when Windows filesystem filters
            # briefly retain a newly created/replaced generation. Never
            # truncate the destination, alter ACLs, or hide a permanent error.
            remaining = deadline - time.monotonic()
            if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33) or remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, .032)


def write_atomic_text(path, text, *, encoding='utf-8'):
    path=Path(path)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    with _publication_lock(path):
        try:
            io_path(temporary).write_text(text,encoding=encoding)
            _replace_snapshot(io_path(temporary),io_path(path))
        finally:
            io_path(temporary).unlink(missing_ok=True)
