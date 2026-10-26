# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Windows notification-area (tray) icon for the background Companion.

Plain Win32 through ctypes: no extra dependency in the packaged Companion.
The icon lives on its own thread with its own message loop; clicks are handed
to the caller's callbacks from that thread, so the caller must marshal them
(the account tray puts them on a queue that Tk drains).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import threading

AVAILABLE = sys.platform == "win32"

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
NIM_ADD, NIM_DELETE = 0, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
MF_STRING, MF_SEPARATOR = 0x0, 0x800
HWND_MESSAGE = -3
COMMAND_OPEN, COMMAND_QUIT = 1, 2

if AVAILABLE:
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                 wintypes.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                    ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
                    ("hBalloonIcon", wintypes.HICON)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.DefWindowProcW.restype = LRESULT
    _user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                       wintypes.LPARAM)
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = (
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
        wintypes.HINSTANCE, wintypes.LPVOID)
    _user32.LoadImageW.restype = wintypes.HANDLE
    _user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT)
    _user32.CreatePopupMenu.restype = wintypes.HMENU
    _user32.AppendMenuW.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                                    wintypes.LPCWSTR)
    _user32.TrackPopupMenu.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, wintypes.HWND, wintypes.LPVOID)
    _user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                     wintypes.LPARAM)
    _user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                    wintypes.UINT)
    _shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def process_alive(pid: int) -> bool:
    """True while process ``pid`` runs (Windows; never signals the process)."""
    if not AVAILABLE or pid <= 0:
        return True
    SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
    handle = _kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return False
    try:
        return _kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        _kernel32.CloseHandle(handle)


class TrayIcon:
    """One tray icon: left click opens, right click shows Open / Quit."""

    def __init__(self, icon_path: str, tooltip: str, *, on_open, on_quit,
                 open_label="Open PolyWorks Account", quit_label="Quit Cloth NeXt Companion"):
        self.icon_path, self.tooltip = icon_path, tooltip[:127]
        self.on_open, self.on_quit = on_open, on_quit
        self.labels = (open_label, quit_label)
        self.hwnd = None
        self.ready = threading.Event()
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, name="Cloth NeXt tray", daemon=True)

    def start(self) -> "TrayIcon":
        self._thread.start()
        self.ready.wait(5.0)
        return self

    def stop(self) -> None:
        if self.hwnd:
            _user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self._thread.join(timeout=3.0)

    def _menu(self, hwnd) -> int:
        menu = _user32.CreatePopupMenu()
        _user32.AppendMenuW(menu, MF_STRING, COMMAND_OPEN, self.labels[0])
        _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        _user32.AppendMenuW(menu, MF_STRING, COMMAND_QUIT, self.labels[1])
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        _user32.SetForegroundWindow(hwnd)  # lets the menu close on an outside click
        chosen = _user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y,
                                        0, hwnd, None)
        _user32.DestroyMenu(menu)
        return chosen

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001 - the Companion keeps working without an icon
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.ready.set()

    def _loop(self) -> None:
        instance = _kernel32.GetModuleHandleW(None)
        data = NOTIFYICONDATAW()

        def window_proc(hwnd, message, wparam, lparam):
            if message == WM_TRAY:
                if lparam == WM_LBUTTONUP:
                    self.on_open()
                elif lparam == WM_RBUTTONUP:
                    chosen = self._menu(hwnd)
                    if chosen == COMMAND_OPEN:
                        self.on_open()
                    elif chosen == COMMAND_QUIT:
                        self.on_quit()
                return 0
            if message == WM_CLOSE:
                _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
                _user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
            return _user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._proc = WNDPROC(window_proc)  # keep the callback alive
        klass = WNDCLASSW(lpfnWndProc=self._proc, hInstance=instance,
                          lpszClassName="ClothNeXtCompanionTray")
        _user32.RegisterClassW(ctypes.byref(klass))
        self.hwnd = _user32.CreateWindowExW(0, klass.lpszClassName, "Cloth NeXt Companion", 0,
                                            0, 0, 0, 0, HWND_MESSAGE, None, instance, None)
        if not self.hwnd:
            raise OSError(ctypes.get_last_error(), "tray window could not be created")
        icon = _user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd, data.uID = self.hwnd, 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage, data.hIcon, data.szTip = WM_TRAY, icon, self.tooltip
        if not _shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise OSError(ctypes.get_last_error(), "tray icon could not be added")
        self.ready.set()
        message = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            _user32.TranslateMessage(ctypes.byref(message))
            _user32.DispatchMessageW(ctypes.byref(message))
        if icon:
            _user32.DestroyIcon(icon)
