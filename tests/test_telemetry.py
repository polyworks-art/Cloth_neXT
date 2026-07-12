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
    service.stop(); assert not any(t.name=="clothnext-telemetry" for t in threading.enumerate())
    assert service.snapshot().solver_process_id is None
