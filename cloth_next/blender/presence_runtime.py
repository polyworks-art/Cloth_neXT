# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Main-thread identity snapshot and reload-safe installation presence timer."""
import time
from pathlib import Path
import bpy
from .. import manifest_version
from ..presence import IdentityStore, Service, payload, INTERVAL, MAX_USERNAME_LENGTH
from .addon_identity import addon_preferences
from . import addon_update_operators

# Retain any in-flight request across a Python module reload, preventing overlap.
_service = globals().get("_service") or Service()
_stale = globals().get("_stale", []) + [cb for cb in
         (globals().get("_pulse"),) if callable(cb)]
_next_send = 0.0
_prompted = False


def store():
    root = bpy.utils.user_resource("CONFIG", path="cloth_next", create=True)
    return IdentityStore(Path(root) / "presence.json", replica=(
        Path(__file__).resolve().parents[1] / "resources" / ".state" / "r7.dat"))


def username_get(self):
    try:
        return store().username()
    except Exception:
        return ""


def username_set(self, value):
    global _next_send
    try:
        store().set_username(value)
        _next_send = 0.0
    except (OSError, ValueError):
        pass  # Invalid edits leave the last accepted value in place.


def _pulse():
    global _next_send, _prompted
    if not _service.active:
        return None
    if time.monotonic() < _next_send:
        return 1.0
    try:
        identity = store()
        identifier = identity.installation_id()
        identity.restore_replica()
        username = identity.username()
        if not username:
            _next_send = time.monotonic() + INTERVAL
            if not _prompted and not getattr(bpy.app, "background", False):
                _prompted = True
                bpy.ops.clothnext.presence_username("INVOKE_DEFAULT")
        elif time.monotonic() >= _next_send:
            _next_send = time.monotonic() + INTERVAL
            preferences = addon_preferences(bpy.context, __package__)
            data = payload(username, identifier, manifest_version(),
                           getattr(preferences, "update_channel",
                                   addon_update_operators.DEFAULT_CHANNEL.name))
            _service.submit(data)
    except Exception:
        # Missing context/configuration is as non-disruptive as network failure.
        _next_send = time.monotonic() + INTERVAL
    return 1.0


def unregister():
    _service.stop()
    for callback in (*_stale, _pulse):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    _stale.clear()


def register():
    global _next_send, _prompted
    if bpy.app.timers.is_registered(_pulse) and _service.active:
        return
    unregister()
    _next_send = 0.0
    _prompted = False
    _service.active = True
    bpy.app.timers.register(_pulse, first_interval=2.0, persistent=True)


class CLOTHNEXT_OT_presence_username(bpy.types.Operator):
    bl_idname = "clothnext.presence_username"
    bl_label = "Superhive Username"
    bl_options = {"INTERNAL"}
    username: bpy.props.StringProperty(name="Superhive Username", default="",
                                       maxlen=MAX_USERNAME_LENGTH)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, confirm_text="Continue")

    def draw(self, context):
        self.layout.prop(self, "username")

    def execute(self, context):
        try:
            store().set_username(self.username)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except OSError:
            return {"CANCELLED"}
        global _next_send
        _next_send = 0.0
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_presence_username,)
