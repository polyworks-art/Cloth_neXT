# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import ctypes, ctypes.wintypes, os

class _MemoryStatus(ctypes.Structure):
    _fields_=[("dwLength",ctypes.c_ulong),("dwMemoryLoad",ctypes.c_ulong),
              ("ullTotalPhys",ctypes.c_ulonglong),("ullAvailPhys",ctypes.c_ulonglong),
              ("ullTotalPageFile",ctypes.c_ulonglong),("ullAvailPageFile",ctypes.c_ulonglong),
              ("ullTotalVirtual",ctypes.c_ulonglong),("ullAvailVirtual",ctypes.c_ulonglong),
              ("ullAvailExtendedVirtual",ctypes.c_ulonglong)]

def _filetime(value) -> int:
    return (value.dwHighDateTime << 32) | value.dwLowDateTime

class WindowsSystemProvider:
    def __init__(self): self._previous=None
    def sample(self) -> tuple[float | None, int | None, int | None]:
        if os.name != "nt": return None, None, None
        kernel=ctypes.windll.kernel32
        idle=ctypes.wintypes.FILETIME(); kernel_t=ctypes.wintypes.FILETIME(); user=ctypes.wintypes.FILETIME()
        if not kernel.GetSystemTimes(ctypes.byref(idle),ctypes.byref(kernel_t),ctypes.byref(user)):
            raise OSError("GetSystemTimes failed")
        current=(_filetime(idle),_filetime(kernel_t)+_filetime(user)); cpu=None
        if self._previous:
            idle_delta=current[0]-self._previous[0]; total=current[1]-self._previous[1]
            if total > 0: cpu=max(0.0,min(100.0,100.0*(1-idle_delta/total)))
        self._previous=current
        mem=_MemoryStatus(); mem.dwLength=ctypes.sizeof(mem)
        if not kernel.GlobalMemoryStatusEx(ctypes.byref(mem)): raise OSError("GlobalMemoryStatusEx failed")
        return cpu, int(mem.ullTotalPhys-mem.ullAvailPhys), int(mem.ullTotalPhys)
