# SPDX-License-Identifier: GPL-3.0-or-later
"""Local account storage shared by the Companion (writer) and Blender (reader).

Layout of the per-OS-user account root (see ``account_root``)::

    installation-id      public, contract 3.3 format, created once
    entitlement          public signed token (verified before every use)
    state.json           public display state written by the Companion
    companion.json       presence heartbeat of a running Companion account window
    session.*            private bearer session (see ``vault``; Companion only)

Only the session is secret. It never leaves the Companion: Blender never
imports ``vault``. Public files are still written atomically with
user-only permissions, and Blender ignores files another user could have
written.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile

ROOT_ENV = "CLOTH_NEXT_ACCOUNT_ROOT"
INSTALLATION_FILE = "installation-id"
ENTITLEMENT_FILE = "entitlement"
# Signed GAIA feature entitlement; read directly by the native TETHYS Engine.
GAIA_ENTITLEMENT_FILE = "gaia-entitlement"
STATE_FILE = "state.json"
PRESENCE_FILE = "companion.json"
LOCK_FILE = "account.lock"
_INSTALLATION_ID = re.compile(r"[A-Za-z0-9_-]{16,128}")


class StorageError(RuntimeError):
    pass


def account_root(environ=None) -> Path:
    """One root per OS user, shared by every Blender version and add-on build.

    Using Blender's version-specific CONFIG folder would mint a new
    installation_id (and burn a device slot) on every Blender upgrade.
    """
    environ = os.environ if environ is None else environ
    override = environ.get(ROOT_ENV)
    if override:
        return Path(override)
    if os.name == "nt":
        base = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "PolyWorks" / "ClothNeXt"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PolyWorks" / "ClothNeXt"
    base = environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "polyworks" / "cloth-next"


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path, 0o700)


def atomic_write(path: Path, data: str | bytes) -> None:
    """Write-then-rename in the same directory; readers never see partial data."""
    ensure_private_directory(path.parent)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def is_trusted_file(path: Path) -> bool:
    """POSIX: a regular file owned by this user and not group/world writable.

    Windows relies on the per-user ACL of %LOCALAPPDATA%.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return False
    import stat
    if not stat.S_ISREG(info.st_mode):
        return False
    if os.name == "nt":
        return True
    return info.st_uid == os.getuid() and not info.st_mode & 0o022


def read_trusted_text(path: Path, limit: int) -> str | None:
    """Return the file text, or None when it is missing, too large or untrusted."""
    if not is_trusted_file(path):
        return None
    try:
        with open(path, "rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


class LocalStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else account_root()

    def path(self, name: str) -> Path:
        return self.root / name

    # Installation identity (contract 3.3) ---------------------------------
    def read_installation_id(self) -> str | None:
        text = read_trusted_text(self.path(INSTALLATION_FILE), 256)
        if text is None:
            return None
        value = text.strip()
        return value if _INSTALLATION_ID.fullmatch(value) else None

    def installation_id(self) -> str:
        """Create once with an exclusive create; never silently replace."""
        path = self.path(INSTALLATION_FILE)
        existing = self.read_installation_id()
        if existing:
            return existing
        if path.exists():
            # A damaged id is never regenerated automatically: that would bind
            # the next activation to a new device slot.
            raise StorageError("The local installation identity is damaged.")
        ensure_private_directory(self.root)
        value = "inst_" + secrets.token_urlsafe(24)  # 32 URL-safe characters
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self.installation_id()
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        return value

    # Entitlement -------------------------------------------------------------
    def read_entitlement(self) -> str | None:
        text = read_trusted_text(self.path(ENTITLEMENT_FILE), 16 * 1024)
        return text.strip() if text else None

    def write_entitlement(self, token: str) -> None:
        atomic_write(self.path(ENTITLEMENT_FILE), token)

    def delete_entitlement(self) -> None:
        self.path(ENTITLEMENT_FILE).unlink(missing_ok=True)

    def read_gaia_entitlement(self) -> str | None:
        text = read_trusted_text(self.path(GAIA_ENTITLEMENT_FILE), 16 * 1024)
        return text.strip() if text else None

    def write_gaia_entitlement(self, token: str) -> None:
        atomic_write(self.path(GAIA_ENTITLEMENT_FILE), token)

    def delete_gaia_entitlement(self) -> None:
        self.path(GAIA_ENTITLEMENT_FILE).unlink(missing_ok=True)

    # Public JSON documents ---------------------------------------------------
    def read_json(self, name: str, limit: int = 256 * 1024):
        text = read_trusted_text(self.path(name), limit)
        if text is None:
            return None
        try:
            value = json.loads(text)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def write_json(self, name: str, value: dict) -> None:
        atomic_write(self.path(name), json.dumps(value, sort_keys=True, indent=1))


@contextmanager
def exclusive_operation(root: Path, *, blocking: bool = False):
    """Cross-process lock serializing account operations of all Companions."""
    ensure_private_directory(Path(root))
    path = Path(root) / LOCK_FILE
    with open(path, "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(stream.fileno(), mode, 1)
            except OSError as exc:
                raise StorageError("Another Cloth NeXt Companion is updating the account.") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except OSError as exc:
                raise StorageError("Another Cloth NeXt Companion is updating the account.") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
