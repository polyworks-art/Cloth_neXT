# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloud Jobs list, job actions and the persistent upload notice.

Everything drawn here comes from ``cloud_runtime.view()``: no registry read,
no network and no file access happens while drawing.
"""

from __future__ import annotations

import bpy

from ..cloud.registry import LocalState
from . import cloud_runtime

_ACTIONS = (
    ("RESUME", "Resume Upload", "Continue the paused upload; nothing is submitted twice"),
    ("PAUSE", "Pause Upload", "Pause the upload; it can be resumed later, also after a restart"),
    ("CANCEL", "Cancel", "Cancel the job; an unsent upload is discarded and costs nothing"),
    ("DOWNLOAD", "Download", "Download the result into the Cloud result archive"),
    ("DELETE_RESULT", "Delete Downloaded Result", "Delete the downloaded raw result from this computer"),
    ("REFRESH", "Refresh", "Ask the Cloud service for the current state of your jobs"),
)
_UPLOAD_STATES = frozenset({LocalState.PACKAGED, LocalState.CREATE_REQUESTED,
                            LocalState.UPLOADING})
_PAUSED_STATES = frozenset({LocalState.UPLOAD_PAUSED})


def actions_for(record) -> tuple[str, ...]:
    state = record.state
    if state in _UPLOAD_STATES:
        return ("PAUSE", "CANCEL")
    if state in _PAUSED_STATES:
        return ("RESUME", "CANCEL")
    if state in (LocalState.QUEUED, LocalState.RUNNING):
        return ("CANCEL",)
    if state in (LocalState.SUCCEEDED, LocalState.STOPPED_AT_LIMIT,
                 LocalState.CANCELLED, LocalState.FAILED):
        return ("DOWNLOAD",)
    if state is LocalState.STORED:
        return ("DELETE_RESULT",)
    return ()


class CLOTHNEXT_OT_cloud_job_action(bpy.types.Operator):
    """Run one action on a Cloud job."""

    bl_idname = "clothnext.cloud_job_action"
    bl_label = "Cloud Job"
    bl_options = {"INTERNAL"}

    action: bpy.props.EnumProperty(items=_ACTIONS)
    client_job_id: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def description(cls, _context, properties):
        for identifier, _label, text in _ACTIONS:
            if identifier == properties.action:
                return text
        return ""

    def invoke(self, context, event):
        if self.action in {"DELETE_RESULT", "CANCEL"}:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, _context):
        if self.action == "PAUSE":
            cloud_runtime.pause_upload()
        elif self.action == "RESUME":
            cloud_runtime.resume_upload(self.client_job_id)
        elif self.action == "CANCEL":
            cloud_runtime.enqueue("cancel", self.client_job_id)
        elif self.action == "DOWNLOAD":
            cloud_runtime.enqueue("download", self.client_job_id)
        elif self.action == "DELETE_RESULT":
            cloud_runtime.enqueue("delete_result", self.client_job_id)
        elif self.action == "REFRESH":
            cloud_runtime.enqueue("refresh")
        return {"FINISHED"}


def draw_jobs(layout) -> None:
    """Global Cloud Jobs list (all jobs of this OS user, any .blend)."""
    view = cloud_runtime.view()
    header = layout.row()
    header.label(text="Cloud Jobs")
    header.operator(CLOTHNEXT_OT_cloud_job_action.bl_idname, text="",
                    icon="FILE_REFRESH").action = "REFRESH"
    if view.message:
        row = layout.row()
        row.alert = bool(view.error_code)
        row.label(text=view.message,
                  icon="ERROR" if view.error_code else "INFO")
    jobs = [job for job in view.jobs if job.state is not LocalState.DISCARDED]
    if not jobs:
        layout.label(text="No Cloud jobs yet")
        return
    for record in reversed(jobs[-20:]):
        box = layout.box()
        top = box.row()
        top.label(text=f"{record.display_name} · {record.engine}")
        for action in actions_for(record):
            label = next(text for identifier, text, _d in _ACTIONS if identifier == action)
            props = top.operator(CLOTHNEXT_OT_cloud_job_action.bl_idname, text=label)
            props.action = action
            props.client_job_id = record.client_job_id
        box.label(text=cloud_runtime.describe_state(record))
        if record.expires_at and record.state not in _UPLOAD_STATES | _PAUSED_STATES:
            box.label(text=f"Available until {record.expires_at}")


def draw_status_notice(self, _context) -> None:
    """Status bar: persistent while an upload or an unclear submit is open."""
    view = cloud_runtime.view()
    if view.uploading:
        total = max(view.upload_total, 1)
        self.layout.label(
            text=f"Uploading to cloud {100 * view.upload_done // total} % – keep Blender open.",
            icon="EXPORT")
    elif any(job.state in (LocalState.UNCONFIRMED, LocalState.SUBMIT_REQUESTED)
             for job in view.jobs):
        self.layout.label(text="Cloud: confirming submission with server…", icon="TIME")


CLASSES = (CLOTHNEXT_OT_cloud_job_action,)


def register() -> None:
    header = getattr(bpy.types, "STATUSBAR_HT_header", None)
    if header is not None and hasattr(header, "append"):
        header.append(draw_status_notice)


def unregister() -> None:
    header = getattr(bpy.types, "STATUSBAR_HT_header", None)
    if header is not None and hasattr(header, "remove"):
        try:
            header.remove(draw_status_notice)
        except (ValueError, RuntimeError):
            pass
