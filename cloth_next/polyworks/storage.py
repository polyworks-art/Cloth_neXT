# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic public entitlement storage; OS credential vault for private sessions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import uuid
import base64
from contextlib import contextmanager


class StorageError(RuntimeError):
    pass


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            name = Path(stream.name)
            os.chmod(name, 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def default_root() -> Path:
    # Companion receives Blender's actual CONFIG path explicitly. Standalone use
    # has a separate default, never guesses a Blender version's config folder.
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'PolyWorks'/'ClothNeXt'
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'polyworks'/'cloth-next'


@contextmanager
def account_operation(root):
    """Serialize Companion operations across processes (refresh-token rotation)."""
    path = Path(root)/'account-operation.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        acquired = False
        try:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise StorageError('Another Companion account operation is running. Please try again.') from exc
            yield
        finally:
            if acquired:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def installation_id(self):
        path = self.root/'installation-id'
        try:
            value = path.read_text(encoding='utf-8').strip()
            uuid.UUID(value)
            return value
        except FileNotFoundError:
            self.root.mkdir(parents=True, exist_ok=True)
            value = str(uuid.uuid4())
            try:
                with path.open('x', encoding='utf-8') as stream:
                    stream.write(value)
                os.chmod(path, 0o600)
            except FileExistsError:
                return self.installation_id()
            return value
        except ValueError as exc:
            # Never silently rebind existing entitlements to a new installation.
            raise StorageError('Installation identity is damaged') from exc

    def read_entitlement(self):
        try:
            path = self.root/'entitlement.jws'
            if path.stat().st_size > 65536:
                raise StorageError('Entitlement exceeds size limit')
            return path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return None

    def write_entitlement(self, token):
        atomic_write(self.root/'entitlement.jws', token)


class CredentialVault:
    """Use only OS-backed keyring providers. No plaintext/fallback-file backend."""
    SERVICE = 'PolyWorks.ClothNeXt.Account.v1'

    def __init__(self, installation_id):
        self.installation_id = installation_id

    def _backend(self):
        try:
            import keyring
            backend = keyring.get_keyring()
            name = type(backend).__module__
            if name not in ('keyring.backends.Windows', 'keyring.backends.macOS', 'keyring.backends.SecretService'):
                raise StorageError('A supported OS credential vault is required')
            return backend
        except ImportError as exc:
            raise StorageError('OS credential vault support is not installed') from exc

    def load(self):
        try:
            raw = self._backend().get_password(self.SERVICE, self.installation_id)
            return json.loads(raw) if raw else None
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError('Could not read the OS credential vault') from exc

    def save(self, value):
        try:
            self._backend().set_password(self.SERVICE, self.installation_id, json.dumps(value))
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError('Could not save the OS credential vault session') from exc

    def clear(self):
        try:
            backend = self._backend()
            if backend.get_password(self.SERVICE, self.installation_id) is not None:
                backend.delete_password(self.SERVICE, self.installation_id)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError('Could not clear the OS credential vault session') from exc


class WindowsProtectedVault:
    """DPAPI user-scope encryption, bound to this installation by entropy.

    Never uses CRYPTPROTECT_LOCAL_MACHINE. Only ciphertext reaches disk.
    This adapter is called exclusively from Companion, not Blender.
    """
    def __init__(self, root, installation_id):
        self.path = Path(root)/'session.dpapi'
        self.entropy = ('PolyWorks.ClothNeXt.Account.v1:'+installation_id).encode()

    def _crypt(self, raw, decrypt=False):
        if os.name != 'nt':
            raise StorageError('Windows protected storage is unavailable')
        import ctypes
        from ctypes import wintypes
        class Blob(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
        source_buffer = ctypes.create_string_buffer(raw)
        entropy_buffer = ctypes.create_string_buffer(self.entropy)
        source = Blob(len(raw), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        entropy = Blob(len(self.entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        library = ctypes.WinDLL('crypt32', use_last_error=True)
        operation = library.CryptUnprotectData if decrypt else library.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
            ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        operation.restype = wintypes.BOOL
        if not operation(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
            raise StorageError('Windows could not unlock the protected account session')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        try:
            return ctypes.string_at(output.data, output.length)
        finally:
            if decrypt:
                ctypes.memset(output.data, 0, output.length)
            kernel.LocalFree(output.data)

    def load(self):
        try:
            if self.path.stat().st_size > 128*1024:
                raise StorageError('Protected account session exceeds size limit')
            data = base64.b64decode(self.path.read_bytes(), validate=True)
            value = json.loads(self._crypt(data, decrypt=True))
            if not isinstance(value, dict):
                raise StorageError('Invalid protected account session')
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise StorageError('Could not read the protected account session') from exc

    def save(self, value):
        raw = json.dumps(value).encode()
        if len(raw) > 65536:
            raise StorageError('Account session exceeds size limit')
        atomic_write(self.path, base64.b64encode(self._crypt(raw)).decode())

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError('Could not remove the protected account session') from exc
