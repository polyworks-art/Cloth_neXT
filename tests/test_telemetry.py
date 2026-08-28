# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import threading, time
import pytest
from cloth_next.telemetry.gpu import parse_nvidia_smi
from cloth_next.telemetry.service import TelemetryService
from cloth_next.telemetry.snapshot import format_bytes

class System:
    def sample(self): return 25.0, 4*1024**3, 16*1024**3

def test_parse_single_and_multiple_gpu_rows():
    one=parse_nvidia_smi("0, RTX 4090, 73, 5940, 24564, 61, 220.5\n")
    assert one[0].index==0 and one[0].utilization_percent==73
    assert one[0].vram_used_bytes==5940*1024**2
    many=parse_nvidia_smi("0, A, 1, 10, 100, N/A, N/A\n1, B, 2, 80, 100, 40, 50\n")
    assert len(many)==2 and many[0].temperature_c is None

def test_malformed_and_formatting():
    with pytest.raises(ValueError): parse_nvidia_smi("0,missing")
    assert format_bytes(None)=="Unavailable"
    assert format_bytes(2*1024**3)=="2.0 GB"

def test_service_cached_stale_start_stop_and_pid():
    calls=0
    def gpu():
        nonlocal calls; calls+=1
        if calls>1: raise OSError("gone")
        return parse_nvidia_smi("0, GPU, 50, 10, 20, 40, 60\n")
    service=TelemetryService(refresh_seconds=.25,stale_seconds=2,gpu_provider=gpu,system_provider=System())
    assert service.start() is True and service.start() is False
    service.set_solver_pid(123); time.sleep(.35)
    snap=service.snapshot(); assert snap.primary_gpu and snap.stale and snap.solver_process_id==123
    assert service.stop() is True
    assert not any(t.name=="clothnext-telemetry" for t in threading.enumerate())
    assert service.snapshot().solver_process_id is None


def test_stop_timeout_keeps_live_worker_registered_and_blocks_duplicate_start():
    entered=threading.Event(); release=threading.Event()
    def gpu():
        entered.set(); release.wait(1); return ()
    service=TelemetryService(refresh_seconds=.25,gpu_provider=gpu,system_provider=System())
    assert service.start() is True; assert entered.wait(1)
    assert service.stop(timeout=0) is False
    assert service.start() is False
    assert service._thread is not None and service._thread.is_alive()
    release.set()
    assert service.stop(timeout=1) is True
    assert service._thread is None


def test_disabled_service_pauses_hardware_queries_without_stopping_thread():
    calls=0
    def gpu():
        nonlocal calls; calls+=1; return ()
    service=TelemetryService(refresh_seconds=.25,gpu_provider=gpu,
                             system_provider=System())
    service.set_enabled(False)
    assert service.start() is True
    time.sleep(.35)
    assert calls == 0
    service.set_enabled(True)
    deadline=time.monotonic()+1.0
    while calls == 0 and time.monotonic() < deadline:
        time.sleep(.02)
    assert calls > 0
    service.set_enabled(False)
    paused_calls=calls
    time.sleep(.35)
    assert calls == paused_calls
    assert service.stop() is True
