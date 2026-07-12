# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import csv, subprocess
from .snapshot import GpuTelemetry

QUERY = ("nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,"
         "memory.total,temperature.gpu,power.draw",
         "--format=csv,noheader,nounits")
TIMEOUT_SECONDS = 2.0
MAX_STDOUT_BYTES = 64 * 1024

def _number(value: str) -> float | None:
    value=value.strip()
    if not value or value.lower() in {"n/a", "[not supported]", "not supported"}:
        return None
    return float(value)

def parse_nvidia_smi(text: str) -> tuple[GpuTelemetry, ...]:
    rows=[]
    for raw in csv.reader(text.splitlines()):
        if not raw: continue
        if len(raw) != 7: raise ValueError("nvidia-smi returned an unexpected column count")
        index=int(raw[0].strip()); used=_number(raw[3]); total=_number(raw[4])
        rows.append(GpuTelemetry(index, raw[1].strip(), _number(raw[2]),
                                 None if used is None else int(used * 1024**2),
                                 None if total is None else int(total * 1024**2),
                                 _number(raw[5]), _number(raw[6])))
    if not rows: raise ValueError("nvidia-smi returned no GPU rows")
    return tuple(rows)

def query_nvidia_smi(*, timeout: float = TIMEOUT_SECONDS) -> tuple[GpuTelemetry, ...]:
    flags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result=subprocess.run(QUERY, shell=False, capture_output=True, text=True,
                          timeout=timeout, creationflags=flags, check=False)
    if result.returncode: raise RuntimeError(f"nvidia-smi exited {result.returncode}")
    if len(result.stdout.encode("utf-8")) > MAX_STDOUT_BYTES:
        raise ValueError("nvidia-smi output exceeded bound")
    return parse_nvidia_smi(result.stdout)
