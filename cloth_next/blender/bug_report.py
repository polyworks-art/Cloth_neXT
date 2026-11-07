# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report a Bug: a dialog in Preferences that sends ``POST /v1/bug-reports``.

The report goes out with the signed-in PolyWorks account. One background
thread does the vault read and the request; ``bpy`` is only touched on the main
thread (operators and a redraw timer). The bearer token never enters this
module's state, logs or reports. The typed text is kept as a draft until the
server confirms the report, so a failed send never loses it. Nothing is sent
while a Bake is running.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace

import bpy

from ..polyworks import bug_reports

LOG = logging.getLogger(__name__)
_lock = threading.Lock()


@dataclass(frozen=True)
class BugReportState:
    sending: bool = False
    message: str = ""          # result of the last send, shown in Preferences
    ok: bool | None = None     # True: sent, False: failed, None: nothing sent yet
    draft: dict = field(default_factory=dict)


_state = {"value": BugReportState(), "worker": None, "redraw": False}


def snapshot() -> BugReportState:
    with _lock:
        return _state["value"]


def _update(**changes) -> None:
    with _lock:
        _state["value"] = replace(_state["value"], **changes)
        _state["redraw"] = True


def busy() -> bool:
    worker = _state["worker"]
    return bool(worker is not None and worker.is_alive())


def _bake_active() -> bool:
    try:
        from ..bake.controller import shared_controller
        return bool(shared_controller.snapshot().active)
    except (ImportError, AttributeError):
        return True  # unknown: never start network work


def environment() -> dict:
    from .. import manifest_version
    version = getattr(bpy.app, "version_string", "") or ""
    return bug_reports.environment_fields(manifest_version(), version)


def _client_name() -> str:
    from .. import manifest_version
    version = getattr(bpy.app, "version_string", "").split(" ")[0]
    return f"Cloth NeXt {manifest_version()} / Blender {version}"[:120]


def _service(client_name: str):
    from ..polyworks.service import AccountService
    return AccountService(client_name=client_name)


def send(fields: dict) -> bool:
    """Validate on the main thread, then send in the background.

    Returns False (with ``message`` set) when nothing was started.
    """
    draft = {name: fields.get(name, "") for name in bug_reports.USER_FIELDS}
    _update(draft=draft)
    if busy():
        _update(ok=False, message="A bug report is still being sent.")
        return False
    if _bake_active():
        _update(ok=False, message=bug_reports.failure_message("bake_active"))
        return False
    try:
        bug_reports.build_report(fields)
    except bug_reports.BugReportInvalid as exc:
        _update(ok=False, message=f"{exc} Nothing was sent.")
        return False
    client_name = _client_name()  # bpy is read here, never on the worker thread

    def job():
        try:
            receipt = _service(client_name).submit_bug_report(fields)
        except bug_reports.BugReportError as exc:
            LOG.info("Bug report not sent: %s", exc.code)
            _update(sending=False, ok=False, message=exc.message)
        except bug_reports.BugReportInvalid as exc:
            _update(sending=False, ok=False, message=f"{exc} Nothing was sent.")
        except Exception as exc:  # noqa: BLE001 - a worker must always finish its state
            LOG.warning("Bug report failed: %s", type(exc).__name__)
            _update(sending=False, ok=False, message=bug_reports.failure_message("unknown"))
        else:
            _update(sending=False, ok=True, message=bug_reports.success_message(receipt),
                    draft={})

    _update(sending=True, ok=None, message="Sending the bug report…")
    worker = threading.Thread(target=job, name="ClothNeXtBugReport", daemon=True)
    _state["worker"] = worker
    worker.start()
    return True


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


def _length_row(layout, name: str, value: str) -> None:
    text = bug_reports.clean_text(value)
    low, high = bug_reports.LIMITS[name]
    problem = bug_reports.field_problem(name, text) if (text or low) else ""
    row = layout.row()
    row.alignment = "RIGHT"
    row.alert = bool(problem) and bool(text)
    row.label(text=f"{len(text)} / {high}" + (f" · at least {low}" if low and len(text) < low else ""))


class CLOTHNEXT_OT_report_bug(bpy.types.Operator):
    """Send a bug report to the Cloth NeXt team with your PolyWorks account. Add-on, Blender and system versions are added automatically"""
    bl_idname = "clothnext.report_bug"
    bl_label = "Report a Bug"
    bl_options = {"INTERNAL"}

    title: bpy.props.StringProperty(
        name="Title", options={"SKIP_SAVE"},
        description="A short summary of the problem (3 to 160 characters)")
    description: bpy.props.StringProperty(
        name="Description", options={"SKIP_SAVE"},
        description="What happened and what you expected (10 to 10000 characters)")
    steps: bpy.props.StringProperty(
        name="Steps", options={"SKIP_SAVE"},
        description="Optional: how to make the problem happen again (up to 5000 characters)")
    details_text: bpy.props.StringProperty(
        name="Longer Text", options={"SKIP_SAVE"},
        description="Optional: a Text from the Text Editor whose content is added to the description. "
                    "Use it for long descriptions or error messages with several lines")

    def invoke(self, context, _event):
        state = snapshot()
        draft = state.draft
        if not state.sending:
            for name in bug_reports.USER_FIELDS:
                if draft.get(name):
                    setattr(self, name, draft[name])
        return context.window_manager.invoke_props_dialog(
            self, width=560, title="Report a Bug", confirm_text="Send Report")

    def _fields(self) -> dict:
        description = self.description
        text = bpy.data.texts.get(self.details_text) if self.details_text else None
        if text is not None:
            extra = text.as_string()
            description = f"{description}\n\n{extra}" if description.strip() else extra
        return {"title": self.title, "description": description, "steps": self.steps,
                **environment()}

    def draw(self, _context):
        layout = self.layout
        state = snapshot()
        if state.sending:
            layout.label(text="The previous report is still being sent.", icon="TIME")
        column = layout.column(align=True)
        column.prop(self, "title")
        _length_row(column, "title", self.title)
        column.prop(self, "description")
        column.prop_search(self, "details_text", bpy.data, "texts", text="Longer Text",
                           icon="TEXT")
        _length_row(column, "description", self._fields()["description"])
        column.prop(self, "steps")
        _length_row(column, "steps", self.steps)
        info = layout.box().column(align=True)
        info.label(text="Sent automatically with your PolyWorks account:", icon="INFO")
        for name, value in environment().items():
            info.label(text=f"{bug_reports.LABELS[name]}: {value or '—'}")
        info.label(text=f"At most {bug_reports.HOURLY_LIMIT} reports per hour.")

    def execute(self, _context):
        if send(self._fields()):
            self.report({"INFO"}, "Sending the bug report in the background.")
            return {"FINISHED"}
        self.report({"WARNING"}, snapshot().message)
        return {"CANCELLED"}


def draw_status(layout) -> None:
    """Result line of the last send for Preferences; no I/O."""
    state = snapshot()
    if not state.message:
        return
    icon = "TIME" if state.sending else "CHECKMARK" if state.ok else "ERROR"
    column = layout.column(align=True)
    words, line = state.message.split(), ""
    lines = []
    for word in words:
        if line and len(line) + len(word) + 1 > 90:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    for index, text in enumerate(lines):
        column.label(text=text, icon=icon if index == 0 else "BLANK1")


CLASSES = (CLOTHNEXT_OT_report_bug,)


def draw_help_menu(self, _context) -> None:
    """Help menu entry: the quickest way to the dialog from anywhere in Blender."""
    self.layout.separator()
    self.layout.operator(CLOTHNEXT_OT_report_bug.bl_idname, text="Report a Cloth NeXt Bug…",
                         icon="ERROR")


def _help_menu():
    return getattr(bpy.types, "TOPBAR_MT_help", None)


def register() -> None:
    _state.update(value=BugReportState(), redraw=False)
    menu = _help_menu()
    if menu is not None:
        menu.remove(draw_help_menu)  # idempotent across reloads
        menu.append(draw_help_menu)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)


def unregister() -> None:
    menu = _help_menu()
    if menu is not None:
        menu.remove(draw_help_menu)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    worker = _state["worker"]
    if worker is not None and worker.is_alive():
        worker.join(timeout=3.0)
    _state.update(worker=None)
