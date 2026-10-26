# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""PolyWorks connection, GAIA access and GAIA installation from Preferences.

One background worker thread does every network, vault, download and
installation step; ``bpy`` is only touched on the main thread (operators and a
redraw timer). Draw callbacks read ``snapshot()`` and never do I/O. The bearer
token stays in the OS-backed vault shared with the Companion; it never enters
this module's state, logs, ``.blend`` files or reports. A Bake blocks every
network and installation step, and nothing here runs during a Bake.

GAIA visibility in the engine menu is a convenience only: the server decides
every download, and the installed package's identity decides what runs.
"""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from dataclasses import dataclass, replace

import bpy

LOG = logging.getLogger(__name__)
_lock = threading.Lock()


@dataclass(frozen=True)
class GaiaState:
    signed_in: bool | None = None       # None: not checked yet
    email: str = ""
    access: str | None = None          # granted | pending | declined | None
    gaia_entitlement: str = ""         # last TETHYS entitlement refresh result
    mode: str = "none"
    downloads_enabled: bool | None = None
    available: dict | None = None      # chosen compatible build (no URL, no token)
    busy: str = ""                      # current step, shown in Preferences
    progress: tuple[int, int] = (0, 0)
    message: str = ""
    error: str = ""
    user_code: str = ""                 # device-link code to confirm in the browser
    checked_at: float | None = None
    installed_build_id: str = ""


_state = {"value": GaiaState(), "worker": None, "cancel": None, "redraw": False, "browser": None}


def snapshot() -> GaiaState:
    with _lock:
        return _state["value"]


LOCAL_ENTITLEMENT_SECONDS = 30.0
_local = {"key": None, "time": 0.0, "value": False}


def local_entitlement_valid() -> bool:
    """A signed, unexpired GAIA entitlement for this installation is stored.

    The same token the TETHYS Engine verifies itself; read from disk at most
    every few seconds (panels call this while drawing).
    """
    from ..polyworks.storage import LocalStore
    now = time.monotonic()
    store = LocalStore()
    try:
        stat = store.path("gaia-entitlement").stat()
        key = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        key = None
    if key == _local["key"] and now - _local["time"] < LOCAL_ENTITLEMENT_SECONDS:
        return _local["value"]
    value = False
    if key is not None:
        try:
            from ..polyworks.entitlements import verify_gaia
            from ..polyworks.trust import load_trust
            token = store.read_gaia_entitlement()
            installation = store.read_installation_id()
            if token and installation:
                verify_gaia(token, trust=load_trust(), installation_id=installation)
                value = True
        except Exception:  # noqa: BLE001 - any doubt means no local proof
            value = False
    _local.update(key=key, time=now, value=value)
    return value


def access_granted() -> bool:
    """Online decision; while that is unknown (offline, check skipped) the
    stored signed GAIA entitlement counts, exactly as the TETHYS Engine sees it."""
    access = snapshot().access
    if access == "granted":
        return True
    return access is None and local_entitlement_valid()


def _update(**changes) -> None:
    with _lock:
        _state["value"] = replace(_state["value"], **changes)
        _state["redraw"] = True


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


def _run(step: str, job) -> bool:
    """Start ``job(cancel_event)`` on the worker thread; False when busy or baking."""
    if busy():
        return False
    if _bake_active():
        _update(error="Account and GAIA actions are unavailable while a Bake is active.")
        return False
    cancel = threading.Event()

    def guarded():
        from ..polyworks.api import ApiError
        from ..polyworks.gaia_downloads import DownloadCancelled, DownloadError
        from ..polyworks.service import ServiceError
        from ..updater.gaia_install import GaiaInstallError
        try:
            job(cancel)
        except ServiceError as exc:
            if exc.code == "not_authenticated":
                _update(signed_in=False, access=None, available=None)
            _update(error=_message(exc.code))
        except ApiError as exc:
            _update(error=_message(exc.code))
        except DownloadCancelled:
            _update(message="GAIA download cancelled. It resumes next time.", error="")
        except DownloadError as exc:
            _update(error=_message(exc.code))
        except GaiaInstallError as exc:
            _update(error=str(exc))
        except (OSError, ValueError) as exc:
            LOG.warning("PolyWorks/GAIA step failed: %s", type(exc).__name__)
            _update(error=f"{step} failed: {type(exc).__name__}. Nothing was changed.")
        finally:
            _update(busy="", progress=(0, 0), user_code="")
    _update(busy=step, error="", message="")
    worker = threading.Thread(target=guarded, name="ClothNeXtGaia", daemon=True)
    _state.update(worker=worker, cancel=cancel)
    worker.start()
    return True


def _refresh_job(service) -> None:
    from .. import manifest_version
    from ..polyworks.gaia_downloads import choose_build
    view = service.view()
    signed_in = view.account_label.startswith("Signed in") and service._token() is not None
    if not signed_in:
        _update(signed_in=False, email="", access=None, available=None, checked_at=time.time())
        return
    access = service.gaia_access()
    available = None
    enabled = None
    if access["status"] == "granted":
        listing = service.gaia_downloads()
        enabled = listing["enabled"]
        available = choose_build(listing["builds"], manifest_version()) if enabled else None
    _update(signed_in=True, email=view.account_identity, access=access["status"], mode=access["mode"],
            downloads_enabled=enabled, available=available, checked_at=time.time())
    _refresh_gaia_entitlement(service)


def _refresh_gaia_entitlement(service) -> None:
    """Keep the signed GAIA entitlement for the TETHYS Engine current.

    Issued and renewed only from the server's GAIA decision; a failure here
    never changes the GAIA access shown above or blocks anything else.
    """
    try:
        _update(gaia_entitlement=service.refresh_gaia_entitlement())
    except Exception as exc:  # noqa: BLE001 - optional during the transition phase
        LOG.info("GAIA entitlement refresh skipped: %s", type(exc).__name__)
        _update(gaia_entitlement="error")


def refresh() -> bool:
    return _run("Checking GAIA access", lambda _cancel: _refresh_job(_service()))


def connect() -> bool:
    """Browser device authorization; the browser opens on the main thread."""
    def job(cancel):
        service = _service()
        with service.operation():
            link = service.start_device_link()
            _update(user_code=link.user_code, message="Confirm the code in your browser.")
            browser = service.endpoints.browser_url(link.verification_uri_complete)
            with _lock:
                _state["browser"] = browser
            while not cancel.is_set():
                if service.poll_device_link(link):
                    break
                time.sleep(0.5)
            else:
                _update(message="Connection cancelled.")
                return
        _update(message="PolyWorks account connected.")
        _refresh_job(service)
    return _run("Connecting PolyWorks account", job)


def disconnect() -> bool:
    def job(_cancel):
        service = _service()
        with service.operation():
            message = service.sign_out()
        _update(signed_in=False, email="", access=None, available=None, message=message)
    return _run("Disconnecting", job)


def request_access() -> bool:
    def job(_cancel):
        status = _service().request_gaia_access()
        _update(access=status, message=("GAIA access requested." if status == "pending"
                                        else "GAIA access granted."))
        if status == "granted":
            _refresh_job(_service())
    return _run("Requesting GAIA access", job)


def redeem_invite(text: str) -> bool:
    def job(_cancel):
        service = _service()
        granted = service.redeem_gaia_invite(text)
        _update(message="Invitation redeemed." if granted else "The invitation did not grant access.")
        _refresh_job(service)
    return _run("Redeeming invitation", job)


def install(*, activate_if_unselected: bool = True) -> bool:
    """Re-check access, open a fresh session, download, verify, install."""
    def job(cancel):
        from .. import manifest_version
        from ..polyworks.gaia_downloads import choose_build, download
        from ..updater.gaia_install import install_gaia_package
        from ..updater.install_paths import ManagedSolverPaths
        from ..updater.solver_registry import load_registry
        from . import preferences
        service = _service()
        access = service.gaia_access()  # the server re-checks entitlement now
        if access["status"] != "granted":
            _update(access=access["status"], available=None,
                    error=_message("gaia_access_required"))
            return
        listing = service.gaia_downloads()
        build = choose_build(listing["builds"], manifest_version()) if listing["enabled"] else None
        if build is None:
            _update(available=None, error="No compatible GAIA build is published for this "
                                          "Cloth NeXt version and computer yet.")
            return
        _update(available=build, progress=(0, build["size_bytes"]))
        if build["build_id"] in _installed_gaia_build_ids(verified=True):
            _update(installed_build_id=build["build_id"],
                    message=f"GAIA {build['version']} is already installed and verified.")
            return
        paths = ManagedSolverPaths.default()
        paths.ensure_layout()
        target = paths.downloads_dir / (build["build_id"].replace("+", "_") + ".zip")
        download(lambda: service.gaia_download_session(build["build_id"]), target,
                 progress=lambda done, total: _update(progress=(done, total)),
                 cancelled=cancel.is_set)
        _update(busy="Verifying and installing GAIA", progress=(0, 0))
        registry = load_registry(paths.registry_json)
        result = install_gaia_package(
            target, paths, probe_version=preferences._probe_version,
            health_check=preferences._health_check, expected_sha256=build["sha256"],
            expected_size=build["size_bytes"], expected_build_id=build["build_id"],
            activate=activate_if_unselected and registry.selected_installation_id is None,
            cancelled=cancel.is_set)
        try:
            target.unlink()
        except OSError:
            pass
        _update(installed_build_id=result.build.build_id,
                message=(f"GAIA {result.build.version} is installed. Choose GAIA as the "
                         "engine to use it." if not result.already_installed
                         else f"GAIA {result.build.version} is already installed and verified."))
    return _run("Downloading GAIA", job)


def cancel() -> None:
    event = _state["cancel"]
    if event is not None:
        event.set()


def _tick():
    with _lock:
        url, _state["browser"] = _state["browser"], None
        redraw, _state["redraw"] = _state["redraw"], False
    if url:
        webbrowser.open(url)
    if redraw:
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == "PREFERENCES":
                        area.tag_redraw()
        except (AttributeError, ReferenceError, RuntimeError):
            pass
    return 0.25


# Operators ---------------------------------------------------------------------------
def _started(operator, ok: bool, text: str):
    if not ok:
        operator.report({"WARNING"}, snapshot().error or "Another account step is still running.")
        return {"CANCELLED"}
    operator.report({"INFO"}, text)
    return {"FINISHED"}


class CLOTHNEXT_OT_polyworks_connect(bpy.types.Operator):
    """Connect a PolyWorks account in your browser. GAIA needs an account with GAIA access; Classic simulation does not"""
    bl_idname = "clothnext.polyworks_connect"
    bl_label = "Connect PolyWorks Account"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        return _started(self, connect(), "Opening your browser to connect the PolyWorks account.")


class CLOTHNEXT_OT_polyworks_disconnect(bpy.types.Operator):
    """Sign this computer out of the PolyWorks account. Classic simulation is not affected"""
    bl_idname = "clothnext.polyworks_disconnect"
    bl_label = "Disconnect"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, _context):
        return _started(self, disconnect(), "Disconnecting the PolyWorks account.")


class CLOTHNEXT_OT_gaia_refresh(bpy.types.Operator):
    """Check GAIA access and the available GAIA build. GAIA needs a PolyWorks account with GAIA access"""
    bl_idname = "clothnext.gaia_refresh"
    bl_label = "Check GAIA Access"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        return _started(self, refresh(), "Checking GAIA access.")


class CLOTHNEXT_OT_gaia_request_access(bpy.types.Operator):
    """Ask PolyWorks for GAIA access"""
    bl_idname = "clothnext.gaia_request_access"
    bl_label = "Request GAIA Access"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        return _started(self, request_access(), "Requesting GAIA access.")


class CLOTHNEXT_OT_gaia_redeem_invite(bpy.types.Operator):
    """Redeem a GAIA invitation link"""
    bl_idname = "clothnext.gaia_redeem_invite"
    bl_label = "Redeem GAIA Invitation"
    bl_options = {"INTERNAL"}

    invite: bpy.props.StringProperty(name="Invitation Link", options={"SKIP_SAVE", "HIDDEN"},
                                     description="Paste the invitation link you received")

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, _context):
        self.layout.prop(self, "invite", text="")

    def execute(self, _context):
        text, self.invite = self.invite, ""
        return _started(self, redeem_invite(text), "Redeeming the invitation.")


class CLOTHNEXT_OT_gaia_install(bpy.types.Operator):
    """Download, verify and install the current GAIA build for this computer"""
    bl_idname = "clothnext.gaia_install"
    bl_label = "Install GAIA"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        build = snapshot().available or {}
        size = build.get("size_bytes") or 0
        return context.window_manager.invoke_confirm(
            self, event, title=f"Install GAIA {build.get('version', '')}".strip(),
            message=(f"Download about {size / 1024 ** 2:.0f} MB from PolyWorks and install "
                     "GAIA next to your existing solvers. The selected engine does not change."),
            confirm_text="Install")

    def execute(self, _context):
        return _started(self, install(), "Downloading GAIA in the background.")


class CLOTHNEXT_OT_gaia_cancel(bpy.types.Operator):
    """Cancel the GAIA download or connection"""
    bl_idname = "clothnext.gaia_cancel"
    bl_label = "Cancel"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        cancel()
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_polyworks_connect, CLOTHNEXT_OT_polyworks_disconnect,
           CLOTHNEXT_OT_gaia_refresh, CLOTHNEXT_OT_gaia_request_access,
           CLOTHNEXT_OT_gaia_redeem_invite, CLOTHNEXT_OT_gaia_install, CLOTHNEXT_OT_gaia_cancel)


INITIAL_RETRY_SECONDS = 30.0


def _initial_check():
    # Interactive sessions only; the job itself sends nothing without a stored session.
    # A Bake or another account step can block the first try: retry until it starts.
    if getattr(bpy.app, "background", False) or refresh():
        return None
    return INITIAL_RETRY_SECONDS


def register() -> None:
    _state["value"] = GaiaState()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    if not bpy.app.timers.is_registered(_initial_check):
        bpy.app.timers.register(_initial_check, first_interval=5.0)


def unregister() -> None:
    for callback in (_tick, _initial_check):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    cancel()
    worker = _state["worker"]
    if worker is not None and worker.is_alive():
        worker.join(timeout=3.0)
    _state.update(worker=None, cancel=None, browser=None)


def draw_gaia_rows(layout, installed: bool = False) -> None:
    """GAIA lines for the solver area; no I/O."""
    state = snapshot()
    column = layout.column(align=True)
    if state.busy:
        row = column.row(align=True)
        done, total = state.progress
        text = state.busy + (f" · {done / 1024 ** 2:.0f} of {total / 1024 ** 2:.0f} MB" if total else "")
        row.label(text=text, icon="TIME")
        row.operator("clothnext.gaia_cancel", text="Cancel")
        if state.user_code:
            column.label(text=f"Browser code: {state.user_code}", icon="URL")
    elif state.signed_in is None:
        if installed:
            # The installed build is already named above; only offer the check.
            column.operator("clothnext.gaia_refresh", text="Check for Updates", icon="FILE_REFRESH")
        else:
            row = column.row()
            row.label(text="Access not checked")
            row.operator("clothnext.gaia_refresh", text="Check Access")
    elif not state.signed_in:
        row = column.row()
        row.label(text="Not signed in")
        row.operator("clothnext.polyworks_connect", text="Connect Account")
    elif state.access != "granted":
        text = {"pending": "GAIA access requested · waiting for PolyWorks",
                "declined": "GAIA access request was declined"}.get(
                    state.access, "This account has no GAIA access yet")
        column.label(text=text, icon="INFO")
        row = column.row(align=True)
        if state.mode != "none" and state.access != "pending":
            row.operator("clothnext.gaia_request_access",
                         text="Request Again" if state.access == "declined" else "Request Access")
        row.operator("clothnext.gaia_redeem_invite", text="Redeem Invitation")
        row.operator("clothnext.gaia_refresh", text="", icon="FILE_REFRESH")
    elif state.available is None:
        row = column.row()
        row.label(text=("GAIA downloads are not available yet." if state.downloads_enabled is False
                        else "No GAIA build is published for this version yet."), icon="INFO")
        row.operator("clothnext.gaia_refresh", text="", icon="FILE_REFRESH")
    else:
        from ..polyworks.gaia_downloads import channel_label
        build = state.available
        label = channel_label(build)
        suffix = f" · {label}" if label else ""
        installed = _installed_gaia_build_ids()
        row = column.row(align=True)
        # Same build_id = same build; any other build_id is offered, even at a lower version.
        if build["build_id"] in installed:
            row.label(text=f"GAIA {build['version']} installed{suffix}", icon="CHECKMARK")
            row.operator("clothnext.gaia_install", text="Repair")
        else:
            row.label(text=f"GAIA {build['version']} available{suffix}", icon="IMPORT")
            row.operator("clothnext.gaia_install", text="Update GAIA" if installed else "Install GAIA")
        row.operator("clothnext.gaia_refresh", text="", icon="FILE_REFRESH")
    if state.error:
        column.label(text=state.error, icon="ERROR")
    elif state.message:
        column.label(text=state.message, icon="INFO")


def _installed_gaia_build_ids(*, verified: bool = False) -> set[str]:
    """Installed GAIA build ids; ``verified`` also checksums the patched files (worker only)."""
    from ..updater import engines
    from . import preferences
    registry, _error = preferences._read_registry()
    ids = set()
    for item in engines.installations_for(registry, engines.GAIA):
        build = engines.gaia_build_of(item)
        if build is not None and (not verified or engines.verify_gaia_files(item.root, build) is None):
            ids.add(build.build_id)
    return ids
