# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Install and update the native engines (AETHER, TETHYS) from Preferences.

The engines are no longer part of the add-on ZIP; they come from the
PolyWorks server like GAIA. AETHER needs no account. TETHYS needs a PolyWorks
account with GAIA access, and the server decides every TETHYS download.

One background worker thread does every network, download and installation
step; ``bpy`` is only touched on the main thread (operators and a redraw
timer). Draw callbacks read ``snapshot()`` and never do I/O beyond a cached
look at the local install pointer. Installing never changes the selected
solver engine. Nothing here runs during a Bake.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace

import bpy

LOG = logging.getLogger(__name__)
_lock = threading.Lock()
ENGINES = ("aether", "tethys")


@dataclass(frozen=True)
class EngineState:
    available: dict | None = None   # newest compatible server build (no URL)
    checked_at: float | None = None
    message: str = ""
    error: str = ""


@dataclass(frozen=True)
class WorkerState:
    engine: str = ""                # engine the running step belongs to
    busy: str = ""
    progress: tuple[int, int] = (0, 0)


_states = {name: EngineState() for name in ENGINES}
_state = {"worker_state": WorkerState(), "worker": None, "cancel": None, "redraw": False}
_installed_cache = {"time": 0.0, "value": {}}


def snapshot(engine: str) -> EngineState:
    with _lock:
        return _states[engine]


def worker_state() -> WorkerState:
    with _lock:
        return _state["worker_state"]


def _update(engine: str, **changes) -> None:
    with _lock:
        _states[engine] = replace(_states[engine], **changes)
        _state["redraw"] = True


def _work(**changes) -> None:
    with _lock:
        _state["worker_state"] = replace(_state["worker_state"], **changes)
        _state["redraw"] = True


def installed(engine: str):
    """The installed engine (``native_engines.InstalledEngine``) or None, cached for drawing."""
    now = time.monotonic()
    with _lock:
        if now - _installed_cache["time"] < 2.0 and engine in _installed_cache["value"]:
            return _installed_cache["value"][engine]
    from ..updater import native_engines
    try:
        value = native_engines.installed(engine)
    except Exception:  # noqa: BLE001 - shown as "not installed", never raised in a draw
        value = None
    with _lock:
        _installed_cache["value"][engine] = value
        _installed_cache["time"] = now
    return value


def _forget_installed() -> None:
    with _lock:
        _installed_cache.update(time=0.0, value={})


def _forget_engine_status(engine: str) -> None:
    """Panels cache engine readiness; drop it so a new install shows at once."""
    try:
        if engine == "aether":
            from . import aether_engine_backend
            aether_engine_backend._status_cache["value"] = None
        else:
            from . import tethys_bake
            tethys_bake._availability_cache["value"] = None
    except (ImportError, AttributeError, KeyError):
        pass


def _spec(engine: str):
    from ..updater import native_engines
    return native_engines.spec(engine)


def _service():
    from .. import manifest_version
    from ..polyworks.service import AccountService
    version = getattr(bpy.app, "version_string", "").split(" ")[0]
    return AccountService(client_name=f"Cloth NeXt {manifest_version()} / Blender {version}"[:120])


def _message(code: str) -> str:
    from ..polyworks import messages
    return messages.companion_message(code)


def _bake_active() -> bool:
    try:
        from ..bake.controller import shared_controller
        return bool(shared_controller.snapshot().active)
    except (ImportError, AttributeError):
        return True  # unknown: never start network or installation work


def busy() -> bool:
    worker = _state["worker"]
    return bool(worker is not None and worker.is_alive())


def _channel() -> str:
    from ..developer import is_dev_build
    return "dev" if is_dev_build() else "public"


def _compatibility(engine: str):
    """(package_compatible, package_probe) of the engine's own module."""
    if engine == "aether":
        from ..aether_engine import engine as module
    else:
        from ..tethys import engine as module
    return module.package_compatible, module.package_probe


def _run(engine: str, step: str, job) -> bool:
    """Start ``job(cancel_event)`` on the worker thread; False when busy or baking."""
    if busy():
        return False
    if _bake_active():
        _update(engine, error="Engine downloads are unavailable while a Bake is active.")
        return False
    cancel = threading.Event()

    def guarded():
        from ..polyworks.api import ApiError
        from ..polyworks.gaia_downloads import DownloadCancelled, DownloadError
        from ..polyworks.service import ServiceError
        from ..updater.native_engines import EngineInstallError
        try:
            job(cancel)
        except (ServiceError, ApiError, DownloadError) as exc:
            if exc.code == "not_found":
                _update(engine, available=None, error=f"No {_spec(engine).label} is published yet.")
            else:
                _update(engine, error=_message(exc.code))
        except DownloadCancelled:
            _update(engine, message="Download cancelled. It resumes next time.", error="")
        except EngineInstallError as exc:
            _update(engine, error=str(exc))
        except (OSError, ValueError) as exc:
            LOG.warning("Engine step failed: %s", type(exc).__name__)
            _update(engine, error=f"{step} failed: {type(exc).__name__}. Nothing was changed.")
        finally:
            _forget_installed()
            _forget_engine_status(engine)
            _work(engine="", busy="", progress=(0, 0))
    _update(engine, error="", message="")
    _work(engine=engine, busy=step, progress=(0, 0))
    worker = threading.Thread(target=guarded, name="ClothNeXtEngines", daemon=True)
    _state.update(worker=worker, cancel=cancel)
    worker.start()
    return True


def _list(service, engine: str) -> dict | None:
    from .. import manifest_version
    from ..updater import native_engines
    item = _spec(engine)
    platform = native_engines.current_platform()
    if item.filename(platform) is None:
        return None
    builds = service.engine_builds(engine, platform=platform, addon_version=manifest_version(),
                                   channel=_channel(), require_account=item.gaia_gated)
    return native_engines.choose_build(builds, engine, manifest_version(), platform=platform)


def _refresh_job(engine: str) -> None:
    if _spec(engine).gaia_gated:
        from . import gaia_account
        if not gaia_account.access_granted():
            _update(engine, available=None, checked_at=time.time(), error="", message="")
            return
    build = _list(_service(), engine)
    _update(engine, available=build, checked_at=time.time())


def refresh(engine: str) -> bool:
    return _run(engine, f"Checking for the {_spec(engine).label}", lambda _cancel: _refresh_job(engine))


def install(engine: str) -> bool:
    """List again, download with a fresh session, verify, install. Never selects an engine."""
    def job(cancel):
        from ..polyworks.gaia_downloads import download
        from ..updater import native_engines
        item = _spec(engine)
        service = _service()
        build = _list(service, engine)
        _update(engine, available=build, checked_at=time.time())
        if build is None:
            _update(engine, error=f"No {item.label} is published for this Cloth NeXt version "
                                  "and computer yet.")
            return
        current = native_engines.installed(engine)
        if (current is not None and current.server_build_id == build["build_id"]
                and native_engines.verify_binary(current.root, current.manifest) is None):
            _update(engine, message=f"{item.label} {current.version} is already installed and verified.")
            return
        root = native_engines.engines_root()
        downloads = root / ".downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        target = downloads / (build["build_id"].replace("+", "_").replace("/", "_") + ".zip")
        _work(busy=f"Downloading the {item.label}", progress=(0, build["size_bytes"]))
        download(lambda: service.engine_download_session(engine, build["build_id"],
                                                         require_account=item.gaia_gated),
                 target, progress=lambda done, total: _work(progress=(done, total)),
                 cancelled=cancel.is_set)
        _work(busy=f"Verifying and installing the {item.label}", progress=(0, 0))
        compatible, probe = _compatibility(engine)
        try:
            result = native_engines.install_package(
                engine, target, compatible=compatible, probe=probe,
                expected_sha256=build["sha256"], expected_size=build["size_bytes"],
                server_build_id=build["build_id"], cancelled=cancel.is_set)
        finally:
            target.unlink(missing_ok=True)
        version = result.installed.version
        _update(engine, message=(f"{item.label} {version} is installed." if not result.already_installed
                                 else f"{item.label} {version} is already installed and verified."))
    return _run(engine, f"Installing the {_spec(engine).label}", job)


def cancel() -> None:
    event = _state["cancel"]
    if event is not None:
        event.set()


def update_available(engine: str) -> dict | None:
    """The listed build when it differs from the installed one (no I/O)."""
    build = snapshot(engine).available
    if build is None:
        return None
    current = installed(engine)
    return None if current is not None and current.server_build_id == build["build_id"] else build


def _tick():
    with _lock:
        redraw, _state["redraw"] = _state["redraw"], False
    if redraw:
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type in {"PREFERENCES", "PROPERTIES"}:
                        area.tag_redraw()
        except (AttributeError, ReferenceError, RuntimeError):
            pass
    return 0.25


# Operators ---------------------------------------------------------------------------
_ENGINE_ITEMS = (("aether", "AETHER", ""), ("tethys", "TETHYS", ""))


def _started(operator, engine: str, ok: bool, text: str):
    if not ok:
        operator.report({"WARNING"}, snapshot(engine).error or "Another engine download is still running.")
        return {"CANCELLED"}
    operator.report({"INFO"}, text)
    return {"FINISHED"}


class CLOTHNEXT_OT_engine_install(bpy.types.Operator):
    """Download, verify and install this engine from PolyWorks. The selected solver engine does not change"""
    bl_idname = "clothnext.engine_install"
    bl_label = "Install Engine"
    bl_options = {"INTERNAL"}

    engine: bpy.props.EnumProperty(items=_ENGINE_ITEMS, options={"SKIP_SAVE", "HIDDEN"})

    def invoke(self, context, event):
        build = snapshot(self.engine).available or {}
        label = _spec(self.engine).label
        size = build.get("size_bytes") or 0
        detail = f"about {size / 1024 ** 2:.0f} MB " if size else ""
        return context.window_manager.invoke_confirm(
            self, event, title=f"Install {label} {build.get('version', '')}".strip(),
            message=f"Download {detail}from PolyWorks and install the {label}. "
                    "The selected solver engine does not change.",
            confirm_text="Install")

    def execute(self, _context):
        label = _spec(self.engine).label
        return _started(self, self.engine, install(self.engine), f"Downloading the {label} in the background.")


class CLOTHNEXT_OT_engine_refresh(bpy.types.Operator):
    """Check PolyWorks for a newer build of this engine"""
    bl_idname = "clothnext.engine_refresh"
    bl_label = "Check for Engine Updates"
    bl_options = {"INTERNAL"}

    engine: bpy.props.EnumProperty(items=_ENGINE_ITEMS, options={"SKIP_SAVE", "HIDDEN"})

    def execute(self, _context):
        return _started(self, self.engine, refresh(self.engine), "Checking for engine updates.")


class CLOTHNEXT_OT_engine_cancel(bpy.types.Operator):
    """Cancel the engine download"""
    bl_idname = "clothnext.engine_cancel"
    bl_label = "Cancel"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        cancel()
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_engine_install, CLOTHNEXT_OT_engine_refresh, CLOTHNEXT_OT_engine_cancel)


INITIAL_RETRY_SECONDS = 30.0


def _startup_job(_cancel) -> None:
    _refresh_job("aether")
    try:
        _refresh_job("tethys")   # sends nothing without GAIA access
    except Exception as exc:  # noqa: BLE001 - AETHER's result stays; TETHYS shows its own error
        LOG.info("TETHYS Engine check skipped: %s", type(exc).__name__)


def _initial_check():
    """Interactive sessions only: look for the AETHER Engine (anonymous) and, with
    GAIA access, the TETHYS Engine. A Bake or another step can block the first try."""
    if getattr(bpy.app, "background", False) or _run("aether", "Checking for engine updates", _startup_job):
        return None
    return INITIAL_RETRY_SECONDS


def after_gaia_check() -> None:
    """GAIA access was just granted (any thread): look for TETHYS once."""
    if snapshot("tethys").checked_at is None and not getattr(bpy.app, "background", False):
        refresh("tethys")


def register() -> None:
    for name in ENGINES:
        _states[name] = EngineState()
    _state["worker_state"] = WorkerState()
    _forget_installed()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    if not bpy.app.timers.is_registered(_initial_check):
        bpy.app.timers.register(_initial_check, first_interval=8.0)


def unregister() -> None:
    for callback in (_tick, _initial_check):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    cancel()
    worker = _state["worker"]
    if worker is not None and worker.is_alive():
        worker.join(timeout=3.0)
    _state.update(worker=None, cancel=None)


def draw_engine_rows(layout, engine: str) -> None:
    """Installed version, install/update/repair and progress; no network I/O."""
    item = _spec(engine)
    state = snapshot(engine)
    work = worker_state()
    current = installed(engine)
    column = layout.column(align=True)
    row = column.row(align=True)
    if work.busy and work.engine == engine:
        done, total = work.progress
        text = work.busy + (f" · {done / 1024 ** 2:.0f} of {total / 1024 ** 2:.0f} MB" if total else "")
        row.label(text=text, icon="TIME")
        row.operator("clothnext.engine_cancel", text="Cancel")
    else:
        build = state.available
        if current is not None:
            row.label(text=f"{item.label} {current.version} installed", icon="CHECKMARK")
        elif build is not None:
            row.label(text=f"{item.label} {build['version']} available", icon="IMPORT")
        else:
            row.label(text=f"{item.label} not installed", icon="INFO")
        action = row.row(align=True)
        action.enabled = not busy()
        if build is not None and (current is None or current.server_build_id != build["build_id"]):
            operator = action.operator("clothnext.engine_install",
                                       text="Update" if current is not None else "Install")
            operator.engine = engine
        operator = action.operator("clothnext.engine_refresh", text="", icon="FILE_REFRESH")
        operator.engine = engine
    if state.error:
        column.label(text=state.error, icon="ERROR")
    elif state.message:
        column.label(text=state.message, icon="INFO")
