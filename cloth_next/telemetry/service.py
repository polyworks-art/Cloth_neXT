# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import subprocess, threading, time
from dataclasses import replace
from .gpu import query_nvidia_smi
from .snapshot import SystemTelemetrySnapshot
from .system import WindowsSystemProvider

class TelemetryService:
    def __init__(self, *, refresh_seconds=1.0, stale_seconds=5.0,
                 gpu_provider=query_nvidia_smi, system_provider=None):
        self._refresh=max(.25,min(10.0,float(refresh_seconds))); self._stale=stale_seconds
        self._gpu=gpu_provider; self._system=system_provider or WindowsSystemProvider()
        self._lock=threading.RLock(); self._stop=threading.Event(); self._thread=None
        self._pid=None; self._snapshot=SystemTelemetrySnapshot()
    def configure(self, refresh_seconds: float):
        with self._lock: self._refresh=max(.25,min(10.0,float(refresh_seconds)))
    def set_solver_pid(self, pid: int | None):
        with self._lock: self._pid=pid if pid and pid > 0 else None
    def snapshot(self):
        with self._lock: return self._snapshot
    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive(): return False
            self._stop.clear(); self._thread=threading.Thread(target=self._run,name="clothnext-telemetry",daemon=False); self._thread.start(); return True
    def stop(self, timeout=3.0):
        self._stop.set(); thread=self._thread
        if thread: thread.join(timeout)
        with self._lock:
            self._thread=None; self._pid=None
            self._snapshot=replace(self._snapshot, solver_process_id=None,
                                   solver_process_memory_bytes=None)
    def _sample(self):
        now=time.time(); errors=[]
        try: gpus=self._gpu()
        except (OSError,ValueError,RuntimeError,TimeoutError,subprocess.TimeoutExpired) as exc: gpus=(); errors.append(str(exc))
        try: cpu,used,total=self._system.sample()
        except (OSError,ValueError) as exc: cpu=used=total=None; errors.append(str(exc))
        with self._lock:
            old=self._snapshot; pid=self._pid
            if not gpus and old.gpus and now-old.updated_at <= self._stale:
                gpus=old.gpus; stale=True
            else: stale=bool(errors)
            self._snapshot=SystemTelemetrySnapshot(gpus,cpu,used,total,pid,None,now,stale,
                                                    "; ".join(errors) if errors else "")
    def _run(self):
        while not self._stop.is_set():
            self._sample()
            with self._lock: interval=self._refresh
            self._stop.wait(interval)

shared_telemetry=TelemetryService()
