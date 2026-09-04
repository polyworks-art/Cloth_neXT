# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reload-safe, fail-open ThreadMark integration for automatic render files."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

import bpy

from .. import manifest_version
from ..bake.companion_bundle import validate_bundle
from ..provenance.worker_protocol import receive_message, send_message
from .threadmark_eligibility import should_threadmark_render

try:
    from bpy.app.handlers import persistent
except (ImportError, ModuleNotFoundError):  # pragma: no cover - pytest fake bpy
    def persistent(function):
        function._bpy_persistent = None
        return function


SUPPORTED_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})
STARTUP_TIMEOUT_SECONDS = 10.0
ENCODE_TIMEOUT_SECONDS = 30.0
SHUTDOWN_TIMEOUT_SECONDS = 1.0


def _identity(path: Path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _absolute(value: str) -> Path:
    try:
        value = bpy.path.abspath(value)
    except (AttributeError, TypeError, ValueError):
        pass
    return Path(value).resolve()


def _output_candidates(scene) -> tuple[Path, ...]:
    render = scene.render
    exact = _absolute(str(render.filepath))
    extension = str(getattr(render, "file_extension", "") or "")
    if bool(getattr(render, "use_file_extension", True)) and extension:
        # Blender preserves explicit compatible aliases such as .jpeg and
        # .tiff even though file_extension reports the canonical .jpg/.tif.
        if not exact.suffix:
            exact = Path(str(exact) + extension)
    try:
        frame = _absolute(str(render.frame_path(frame=scene.frame_current)))
    except (AttributeError, TypeError, ValueError):
        frame = exact
    return tuple(dict.fromkeys((exact, frame)))


class OwnedThreadMarkWorker:
    """One authenticated helper child, retained only for one render session."""

    def __init__(self):
        self.process = None
        self.connection = None
        self.listener = None
        self.token = secrets.token_hex(32)

    def _command(self, port: int) -> list[str]:
        extension_root = Path(__file__).resolve().parents[1]
        helper = os.environ.get("CLOTH_NEXT_THREADMARK_HELPER")
        if helper:
            return [
                helper,
                "--threadmark-worker",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--token",
                self.token,
            ]
        if os.environ.get("CLOTH_NEXT_DEVELOPER_THREADMARK") == "1":
            python = os.environ.get("CLOTH_NEXT_THREADMARK_PYTHON", sys.executable)
            return [
                python,
                "-m",
                "verifier.app",
                "--threadmark-worker",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--token",
                self.token,
            ]
        executable = validate_bundle(extension_root, manifest_version())
        return [
            str(executable),
            "--mode",
            "threadmark-worker",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--token",
            self.token,
        ]

    def start(self) -> None:
        if self.connection is not None:
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener = listener
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(STARTUP_TIMEOUT_SECONDS)
        port = listener.getsockname()[1]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            self._command(port),
            cwd=Path(__file__).resolve().parents[2],
            shell=False,
            creationflags=creationflags,
        )
        try:
            connection, address = listener.accept()
            if address[0] != "127.0.0.1":
                connection.close()
                raise ConnectionError("ThreadMark worker was not loopback")
            connection.settimeout(ENCODE_TIMEOUT_SECONDS)
            ready = receive_message(connection)
            if ready != {"type": "ready", "token": self.token}:
                connection.close()
                raise ConnectionError("ThreadMark worker handshake failed")
            self.connection = connection
        except Exception:
            self.shutdown()
            raise
        finally:
            listener.close()
            self.listener = None

    def encode(self, path: Path) -> tuple[bool, str]:
        self.start()
        send_message(
            self.connection,
            {"type": "encode", "token": self.token, "path": str(path)},
        )
        response = receive_message(self.connection)
        return bool(response.get("ok")), str(response.get("reason", ""))[:128]

    def shutdown(self) -> None:
        connection, self.connection = self.connection, None
        process, self.process = self.process, None
        listener, self.listener = self.listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if connection is not None:
            try:
                connection.settimeout(SHUTDOWN_TIMEOUT_SECONDS)
                send_message(
                    connection, {"type": "shutdown", "token": self.token}
                )
                receive_message(connection)
            except (ConnectionError, OSError, TypeError, ValueError):
                pass
            finally:
                connection.close()
        if process is None:
            return
        try:
            process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)


@dataclass
class ThreadMarkRenderSession:
    active: bool = False
    eligible: bool = False
    processed: set[tuple[int, str]] = field(default_factory=set)
    before_write: dict[Path, tuple[int, int] | None] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)
    worker: object | None = None


_session = ThreadMarkRenderSession()
_worker_factory = OwnedThreadMarkWorker
_registered = False


def _diagnostic(message: str) -> None:
    bounded = str(message).replace("\n", " ")[:256]
    if len(_session.diagnostics) < 64:
        _session.diagnostics.append(bounded)
    print(f"Cloth NeXt ThreadMark: {bounded}")


def _clear_session() -> None:
    worker = _session.worker
    if worker is not None:
        try:
            worker.shutdown()
        except Exception as exc:  # fail-open cleanup of the exact owned child
            _diagnostic(f"worker shutdown failed ({type(exc).__name__})")
    _session.active = False
    _session.eligible = False
    _session.processed.clear()
    _session.before_write.clear()
    _session.diagnostics.clear()
    _session.worker = None


@persistent
def _on_render_pre(scene, *_args) -> None:
    try:
        if not _session.active:
            _clear_session()
            _session.active = True
            _session.eligible = bool(should_threadmark_render(scene))
            _diagnostic(f"eligible: {'yes' if _session.eligible else 'no'}")
        _session.before_write = {
            path: _identity(path) for path in _output_candidates(scene)
        }
    except Exception as exc:
        _session.eligible = False
        _diagnostic(f"eligibility failed ({type(exc).__name__})")


@persistent
def _on_render_write(scene, *_args) -> None:
    if not _session.active or not _session.eligible:
        return
    try:
        changed = [
            path
            for path in _output_candidates(scene)
            if _identity(path) is not None
            and _identity(path) != _session.before_write.get(path)
        ]
        changed = list(dict.fromkeys(changed))
        if len(changed) != 1:
            _diagnostic("output path was not uniquely identifiable; original preserved")
            return
        path = changed[0]
        key = (int(scene.frame_current), str(path))
        if key in _session.processed:
            return
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            _diagnostic(f"unsupported output format {path.suffix.lower() or '<none>'}; skipped")
            _session.processed.add(key)
            return
        if _session.worker is None:
            _session.worker = _worker_factory()
        ok, reason = _session.worker.encode(path)
        if ok:
            _session.processed.add(key)
            _diagnostic(f"encoded frame {int(scene.frame_current)}")
        else:
            _diagnostic(f"encode failed ({reason or 'worker rejected output'}); original preserved")
    except Exception as exc:
        _diagnostic(f"encode failed ({type(exc).__name__}); original preserved")


@persistent
def _on_render_complete(*_args) -> None:
    _clear_session()


@persistent
def _on_render_cancel(*_args) -> None:
    _clear_session()


@persistent
def _on_load_pre(*_args) -> None:
    _clear_session()


_HANDLER_SLOTS = (
    ("render_pre", "_on_render_pre"),
    ("render_write", "_on_render_write"),
    ("render_complete", "_on_render_complete"),
    ("render_cancel", "_on_render_cancel"),
    ("load_pre", "_on_load_pre"),
)
for _slot, _name in _HANDLER_SLOTS:
    globals()[_name]._clothnext_threadmark_handler = True


def _purge_stale(container) -> None:
    live = {globals()[name] for _slot, name in _HANDLER_SLOTS}
    for callback in list(container):
        if (
            getattr(callback, "_clothnext_threadmark_handler", False)
            and callback not in live
        ):
            container.remove(callback)


def register() -> None:
    global _registered
    if _registered:
        return
    _clear_session()
    for slot, name in _HANDLER_SLOTS:
        container = getattr(bpy.app.handlers, slot)
        _purge_stale(container)
        callback = globals()[name]
        if callback not in container:
            container.append(callback)
    _registered = True


def unregister() -> None:
    global _registered
    for slot, name in _HANDLER_SLOTS:
        container = getattr(bpy.app.handlers, slot, None)
        if container is None:
            continue
        callback = globals()[name]
        while callback in container:
            container.remove(callback)
        _purge_stale(container)
    _clear_session()
    _registered = False


def handler_count() -> int:
    return sum(
        sum(
            1
            for callback in getattr(bpy.app.handlers, slot, ())
            if getattr(callback, "_clothnext_threadmark_handler", False)
        )
        for slot, _name in _HANDLER_SLOTS
    )
