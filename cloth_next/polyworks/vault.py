# SPDX-License-Identifier: GPL-3.0-or-later
"""Private bearer-session storage. Companion only; Blender never imports this.

Contract 2: the OS keyring if possible, otherwise a user-only file. Windows uses
user-scope DPAPI ciphertext; macOS/Linux use Keychain/Secret Service through
``keyring`` when available, else a 0600 file inside the 0700 account folder.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from .storage import StorageError, atomic_write, is_trusted_file


class UserFileVault:
    """Contract 2: "otherwise in a user-only file" (0600 inside a 0700 folder)."""
    kind = "user-only file"

    def __init__(self, root: Path):
        self.path = Path(root) / "session.json"

    def load(self):
        if not self.path.exists():
            return None
        if not is_trusted_file(self.path):
            raise StorageError("The saved session file has unsafe permissions.")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StorageError("The saved session could not be read.") from exc
        return value if isinstance(value, dict) else None

    def save(self, value: dict) -> None:
        atomic_write(self.path, json.dumps(value))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class KeyringVault:
    """macOS Keychain or Secret Service through ``keyring``; never its plaintext
    or fallback backends."""
    kind = "OS keyring"
    SERVICE = "PolyWorks.ClothNeXt.Session.v1"
    USER = "addon-session"
    SUPPORTED = ("keyring.backends.macOS", "keyring.backends.SecretService",
                 "keyring.backends.Windows")

    def __init__(self, backend):
        self.backend = backend

    @classmethod
    def available(cls):
        try:
            import keyring
            backend = keyring.get_keyring()
        except Exception:  # keyring raises backend-specific errors at discovery
            return None
        return cls(backend) if type(backend).__module__ in cls.SUPPORTED else None

    def load(self):
        try:
            raw = self.backend.get_password(self.SERVICE, self.USER)
            value = json.loads(raw) if raw else None
        except Exception as exc:
            raise StorageError("The OS keyring could not be read.") from exc
        return value if isinstance(value, dict) else None

    def save(self, value: dict) -> None:
        try:
            self.backend.set_password(self.SERVICE, self.USER, json.dumps(value))
        except Exception as exc:
            raise StorageError("The OS keyring could not store the session.") from exc

    def clear(self) -> None:
        try:
            if self.backend.get_password(self.SERVICE, self.USER) is not None:
                self.backend.delete_password(self.SERVICE, self.USER)
        except Exception as exc:
            raise StorageError("The OS keyring could not remove the session.") from exc


class WindowsDpapiVault:
    """User-scope DPAPI (never CRYPTPROTECT_LOCAL_MACHINE); ciphertext on disk."""
    kind = "Windows DPAPI"
    ENTROPY = b"PolyWorks.ClothNeXt.Session.v1"

    def __init__(self, root: Path):
        self.path = Path(root) / "session.dpapi"

    def _crypt(self, raw: bytes, decrypt: bool) -> bytes:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        source_buffer = ctypes.create_string_buffer(raw, len(raw))
        entropy_buffer = ctypes.create_string_buffer(self.ENTROPY, len(self.ENTROPY))
        source = Blob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        entropy = Blob(len(self.ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        operation.restype = wintypes.BOOL
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not operation(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                         CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output)):
            raise StorageError("Windows could not unlock the saved session.")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        try:
            return ctypes.string_at(output.data, output.length)
        finally:
            if decrypt:
                ctypes.memset(output.data, 0, output.length)
            kernel32.LocalFree(output.data)

    def load(self):
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageError("The saved session could not be read.") from exc
        try:
            value = json.loads(self._crypt(base64.b64decode(data, validate=True), True))
        except ValueError as exc:
            raise StorageError("The saved session is damaged.") from exc
        return value if isinstance(value, dict) else None

    def save(self, value: dict) -> None:
        atomic_write(self.path, base64.b64encode(self._crypt(json.dumps(value).encode(), False)))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def default_vault(root: Path):
    if os.name == "nt":
        return WindowsDpapiVault(root)
    return KeyringVault.available() or UserFileVault(root)
