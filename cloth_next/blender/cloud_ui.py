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
    ("REVIEW", "Review & Send", "Check the quoted cost limit and send the job"),
    ("REQUOTE", "Get New Quote", "Request a new binding cost limit; nothing is sent yet"),
    ("RESUME", "Resume Upload", "Continue the paused upload; nothing is submitted twice"),
    ("PAUSE", "Pause Upload", "Pause the upload; it can be resumed later, also after a restart"),
    ("CANCEL", "Cancel", "Cancel the job; an unsent upload is discarded and costs nothing"),
    ("DOWNLOAD", "Download", "Download the result into the Cloud result archive"),
    ("ACTIVATE", "Activate", "Create the playback cache from the downloaded result and attach it"),
    ("ACTIVATE_ANYWAY", "Activate Anyway",
     "Attach although settings or geometry changed or a newer local Bake exists"),
    ("DELETE_RESULT", "Delete Downloaded Result", "Delete the downloaded raw result from this computer"),
    ("REFRESH", "Refresh", "Ask the Cloud service for the current state of your jobs"),
)
_UPLOAD_STATES = frozenset({LocalState.PACKAGED, LocalState.CREATE_REQUESTED,
                            LocalState.UPLOADING})
_PAUSED_STATES = frozenset({LocalState.UPLOAD_PAUSED})


def actions_for(record) -> tuple[str, ...]:
    state = record.state
    if state is LocalState.AWAITING_APPROVAL:
        return ("REVIEW", "CANCEL")
    if state in _UPLOAD_STATES:
        return ("PAUSE", "CANCEL")
    if state in _PAUSED_STATES:
        if not getattr(record, "quote_accepted", True):
            return ("REQUOTE", "CANCEL")
        return ("RESUME", "CANCEL")
    if state in (LocalState.QUEUED, LocalState.RUNNING):
        return ("CANCEL",)
    if state is LocalState.SUCCEEDED:
        # Cloud API v1 publishes results only for complete success.
        return ("DOWNLOAD",)
    if state is LocalState.STORED:
        return ("ACTIVATE", "DELETE_RESULT")
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
        if self.action == "REVIEW":
            return bpy.ops.clothnext.cloud_review("INVOKE_DEFAULT",
                                                  client_job_id=self.client_job_id)
        if self.action in {"DELETE_RESULT", "CANCEL", "ACTIVATE_ANYWAY"}:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        if self.action in {"ACTIVATE", "ACTIVATE_ANYWAY"}:
            return self._activate(context, self.action == "ACTIVATE_ANYWAY")
        if self.action == "REQUOTE":
            cloud_runtime.enqueue("quote", self.client_job_id)
            return {"FINISHED"}
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

    def _activate(self, context, confirmed):
        from ..cloud import errors
        from ..cloud.activation import Verdict
        from . import cloud_activation
        try:
            decision = cloud_activation.activate(context, self.client_job_id,
                                                 confirmed=confirmed)
        except Exception as exc:  # noqa: BLE001 - trusted text only
            _code, _info, text = errors.describe(errors.code_of(exc))
            detail = str(exc) if isinstance(exc, cloud_activation.ActivationRefused) else ""
            self.report({"ERROR"}, f"{text} {detail}".strip())
            return {"CANCELLED"}
        if decision.verdict is not Verdict.READY and not confirmed:
            _PENDING_DECISIONS[self.client_job_id] = decision.verdict.value
            self.report({"WARNING"}, "; ".join(text for _u, verdict, text in
                                               decision.per_object
                                               if verdict is not Verdict.READY)
                        + ". Use Activate Anyway to attach it.")
            return {"CANCELLED"}
        _PENDING_DECISIONS.pop(self.client_job_id, None)
        self.report({"INFO"}, "Cloud result activated")
        return {"FINISHED"}


def review_lines(record, manifest, capabilities=None) -> list[str]:
    """Confirmation content (concept 12.3) from the stored package + quote."""
    objects = manifest.get("objects", ())
    vertices = sum(int(item.get("vertex_count", 0)) for item in objects)
    total = sum(int(item.get("size_bytes", 0)) for item in manifest.get("files", ()))
    water = {"hydrodynamic": "Water Flow · Hydrodynamic Pressure",
             "native-fluid": "Water Flow · Native Flow"}.get(
                 manifest.get("water_mode"), "No Water Flow")
    lines = [
        f"{manifest.get('engine', '')} · build {manifest.get('build_id', '')}",
        f"Frames {manifest.get('frame_start')}–{manifest.get('frame_end')} · "
        f"{manifest.get('fps', 0):g} fps",
        f"{len(objects)} object(s) · {vertices:,} vertices · {water}",
        f"Upload up to {total / 1e6:.1f} MB (data you already sent is not sent again)",
        f"Cost limit: {record.quote_maximum:g} {record.quote_unit} · "
        f"quote valid until {record.quote_expires_at}",
    ]
    if record.quote_billing_mode == "test":
        lines.append("Test billing only: no real charge is made.")
    retention = getattr(capabilities, "retention_days", None)
    if retention:
        lines.append(f"Results stay available for {retention} days.")
    lines.append("Scene geometry and simulation settings are sent to the "
                 "PolyWorks Cloud and deleted after the retention period.")
    return lines


class CLOTHNEXT_OT_cloud_review(bpy.types.Operator):
    """Review the binding cost limit and send the Cloud job."""

    bl_idname = "clothnext.cloud_review"
    bl_label = "Send to Cloud"
    bl_options = {"INTERNAL"}

    client_job_id: bpy.props.StringProperty(options={"HIDDEN"})
    quote_id: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, _event):
        from ..cloud.jobs import load_manifest
        from ..cloud import service
        runtime = cloud_runtime.jobs()
        record = runtime.registry.get(self.client_job_id) if runtime else None
        if record is None or record.state is not LocalState.AWAITING_APPROVAL:
            self.report({"ERROR"}, "This Cloud job has no quote to confirm.")
            return {"CANCELLED"}
        self.quote_id = record.quote_id
        self._lines = review_lines(record, load_manifest(runtime.store,
                                                         record.manifest_sha256),
                                   service.availability().capabilities)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        for line in getattr(self, "_lines", ()):
            self.layout.label(text=line)

    def execute(self, _context):
        try:
            cloud_runtime.approve(self.client_job_id, self.quote_id)
        except Exception as exc:  # noqa: BLE001 - trusted text only
            from ..cloud import errors
            self.report({"ERROR"}, errors.describe(errors.code_of(exc))[2])
            return {"CANCELLED"}
        return {"FINISHED"}


# Last activation verdict per job that needs a decision (main thread only).
_PENDING_DECISIONS: dict[str, str] = {}


SCENE_JOBS_PROPERTY = "cloth_next_cloud_job_ids"


def remember_job(scene, client_job_id: str) -> None:
    """Store the job in the .blend so it is found again after a move/rename."""
    ids = [value for value in str(getattr(scene, SCENE_JOBS_PROPERTY, "") or "").split(",")
           if value]
    if client_job_id not in ids:
        ids.append(client_job_id)
        setattr(scene, SCENE_JOBS_PROPERTY, ",".join(ids[-200:]))


def file_jobs(scene, records) -> tuple:
    """Jobs sent from this .blend: stored ids, else matching object UUIDs (T10)."""
    from .. import export_identity
    ids = set(str(getattr(scene, SCENE_JOBS_PROPERTY, "") or "").split(","))
    uuids = set()
    for obj in getattr(scene, "objects", ()):
        settings = getattr(obj, "cloth_next", None)
        if (settings is not None and getattr(settings, "enabled", False)
                and str(getattr(settings, "persistent_export_id", "") or "")):
            try:
                uuids.add(export_identity.export_uuid(obj))
            except (ValueError, AttributeError):
                continue
    return tuple(record for record in records
                 if record.client_job_id in ids or uuids.intersection(record.object_uuids))


def draw_jobs(layout, *, scene=None) -> None:
    """Cloud Jobs list: all jobs of this OS user, or only this .blend's."""
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
    if scene is not None:
        jobs = list(file_jobs(scene, jobs))
    if not jobs:
        layout.label(text="No Cloud jobs for this file" if scene is not None
                     else "No Cloud jobs yet")
        return
    for record in reversed(jobs[-20:]):
        box = layout.box()
        top = box.row()
        top.label(text=f"{record.display_name} · {record.engine}")
        actions = list(actions_for(record))
        if record.client_job_id in _PENDING_DECISIONS and "ACTIVATE" in actions:
            actions[actions.index("ACTIVATE")] = "ACTIVATE_ANYWAY"
        for action in actions:
            label = next(text for identifier, text, _d in _ACTIONS if identifier == action)
            props = top.operator(CLOTHNEXT_OT_cloud_job_action.bl_idname, text=label)
            props.action = action
            props.client_job_id = record.client_job_id
        box.label(text=cloud_runtime.describe_state(record))
        pending = _PENDING_DECISIONS.get(record.client_job_id)
        if pending:
            box.label(text={"STALE_SETTINGS": "Settings changed since sending",
                            "STALE_GEOMETRY": "Geometry changed since sending",
                            "NEWER_LOCAL": "A newer local Bake exists"}.get(pending, pending),
                      icon="ERROR")
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


_auto_seen: set[str] = set()
# Jobs downloaded in this session; only these activate automatically, so a
# restart never re-attaches an older stored result over newer work.
_downloaded_here: set[str] = set()


def _auto_step() -> None:
    """Main thread: automatic download, then automatic activation (E9).

    Download only while Blender runs and the preference is on (free space is
    checked by the download itself). Activation only when nothing relevant
    changed and no newer local result would be replaced.
    """
    from .addon_identity import addon_preferences
    try:
        prefs = addon_preferences(bpy.context, __package__)
    except KeyError:
        return
    if not getattr(prefs, "cloud_auto_download", False):
        return
    for record in cloud_runtime.view().jobs:
        key = f"{record.client_job_id}:{record.state.value}"
        if key in _auto_seen:
            continue
        if record.state is LocalState.SUCCEEDED:
            _auto_seen.add(key)
            _downloaded_here.add(record.client_job_id)
            cloud_runtime.enqueue("download", record.client_job_id)
        elif (record.state is LocalState.STORED
              and record.client_job_id in _downloaded_here):
            _auto_seen.add(key)
            from . import cloud_activation
            try:
                decision = cloud_activation.activate(bpy.context, record.client_job_id)
            except Exception:  # noqa: BLE001 - stays stored; the list offers Activate
                continue
            if not decision.may_auto_activate:
                _PENDING_DECISIONS[record.client_job_id] = decision.verdict.value


CLASSES = (CLOTHNEXT_OT_cloud_job_action, CLOTHNEXT_OT_cloud_review)


def register() -> None:
    scene_type = getattr(bpy.types, "Scene", None)
    if scene_type is not None:
        setattr(scene_type, SCENE_JOBS_PROPERTY, bpy.props.StringProperty(
            name="Cloud Jobs", default="", options={"HIDDEN"},
            description="Cloud jobs sent from this file"))
    if _auto_step not in cloud_runtime._main_thread_hooks:
        cloud_runtime._main_thread_hooks.append(_auto_step)
    header = getattr(bpy.types, "STATUSBAR_HT_header", None)
    if header is not None and hasattr(header, "append"):
        header.append(draw_status_notice)


def unregister() -> None:
    if _auto_step in cloud_runtime._main_thread_hooks:
        cloud_runtime._main_thread_hooks.remove(_auto_step)
    scene_type = getattr(bpy.types, "Scene", None)
    if scene_type is not None and hasattr(scene_type, SCENE_JOBS_PROPERTY):
        try:
            delattr(scene_type, SCENE_JOBS_PROPERTY)
        except (AttributeError, RuntimeError):
            pass
    header = getattr(bpy.types, "STATUSBAR_HT_header", None)
    if header is not None and hasattr(header, "remove"):
        try:
            header.remove(draw_status_notice)
        except (ValueError, RuntimeError):
            pass
