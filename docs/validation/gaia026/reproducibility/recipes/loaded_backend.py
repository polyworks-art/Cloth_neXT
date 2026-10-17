"""Read-only Windows DLL identity capture for native replay evidence."""
import ctypes
from ctypes import wintypes
from pathlib import Path

def loaded_cuda_backend(pid):
 kernel=ctypes.WinDLL('kernel32',use_last_error=True);psapi=ctypes.WinDLL('psapi',use_last_error=True)
 kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
 kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
 psapi.EnumProcessModulesEx.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.HMODULE),wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.DWORD];psapi.EnumProcessModulesEx.restype=wintypes.BOOL
 psapi.GetModuleFileNameExW.argtypes=[wintypes.HANDLE,wintypes.HMODULE,wintypes.LPWSTR,wintypes.DWORD];psapi.GetModuleFileNameExW.restype=wintypes.DWORD
 handle=kernel.OpenProcess(0x0410,False,pid)
 if not handle:return None
 try:
  modules=(wintypes.HMODULE*2048)();needed=wintypes.DWORD()
  if not psapi.EnumProcessModulesEx(handle,modules,ctypes.sizeof(modules),ctypes.byref(needed),3):return None
  for module in modules[:min(2048,needed.value//ctypes.sizeof(wintypes.HMODULE))]:
   name=ctypes.create_unicode_buffer(32768)
   if psapi.GetModuleFileNameExW(handle,module,name,len(name)) and Path(name.value).name.lower()=='libsimbackend_cuda.dll':return str(Path(name.value).resolve())
 finally:kernel.CloseHandle(handle)
 return None
