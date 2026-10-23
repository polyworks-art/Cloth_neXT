# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender side of Cloud Bakes: one worker thread, cached views, a timer.

Networking and file transfer run on ``clothnext-cloud`` only. The main
thread queues work, reads ``view()`` (an immutable snapshot refreshed by the
worker) and never waits for the network; panels draw from that snapshot.
The worker never touches ``bpy``.
"""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time

from ..cloud import errors, service
from ..cloud.jobs import CloudJobs, UploadPaused
from ..cloud.package_store import PackageStore, default_root as package_root
from ..cloud.registry import JobRecord, JobRegistry, LocalState
from ..cloud.results import ResultArchive, default_root as results_root
from ..core.logging import get_logger, log_with_context

_LOG = get_logger("cloud.runtime")
REFRESH_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class CloudView:
    jobs: tuple[JobRecord, ...] = ()
    uploading: bool = False
    upload_done: int = 0
    upload_total: int = 0
    message: str = ""
    error_code: str = ""


_lock = threading.Lock()
_view = CloudView()
_tasks: "queue.Queue[tuple[str, str]]" = queue.Queue()
_worker: threading.Thread | None = None
_stop = threading.Event()
_pause = threading.Event()
_jobs: CloudJobs | None = None
_last_refresh = 0.0


def view() -> CloudView:
    with _lock:
        return _view


def _publish(**changes) -> None:
    global _view
    with _lock:
        values = {name: getattr(_view, name) for name in CloudView.__dataclass_fields__}
        values.update(changes)
        _view = CloudView(**values)


def _make_jobs() -> CloudJobs | None:
    backend = service.backend()
    if backend is None:
        return None
    return CloudJobs(backend, JobRegistry(), PackageStore(package_root()),
                     ResultArchive(results_root()))


def jobs() -> CloudJobs | None:
    global _jobs
    if _jobs is None:
        _jobs = _make_jobs()
    return _jobs


def reset() -> None:
    """Forget the cached service objects (backend changed)."""
    global _jobs
    _jobs = None


def package_plan(plan, deformables, *, check_cancel, event):
    """Bake worker thread: package an exported plan (no ``bpy``)."""
    from .. import manifest_version
    from ..cloud.package import build_package
    runtime = jobs()
    if runtime is None:
        raise RuntimeError(service.PENDING_REASON)
    names = sorted({target.object_name for target in deformables})
    title = names[0] if len(names) == 1 else f"{names[0]} + {len(names) - 1}"
    return build_package(
        plan, deformables, store=runtime.store,
        work_directory=plan.work_directory / "cloud-package",
        addon_version=manifest_version(), blender_version=_BLENDER_VERSION,
        job_name=f"{title} · {plan.frame_start}–{plan.frame_end}",
        check_cancel=check_cancel, event=event)


_BLENDER_VERSION = "unknown"


def set_blender_version(value: str) -> None:
    global _BLENDER_VERSION
    _BLENDER_VERSION = str(value)


# Main-thread API ---------------------------------------------------------------

def enqueue(action: str, client_job_id: str = "") -> None:
    _ensure_worker()
    _tasks.put((action, client_job_id))


def submit(package) -> JobRecord:
    """Register a packaged Bake and start its upload in the background."""
    runtime = jobs()
    if runtime is None:
        raise RuntimeError(service.PENDING_REASON)
    record = runtime.register(package)
    _publish(jobs=runtime.registry.all())
    enqueue("upload", record.client_job_id)
    return record


def pause_upload() -> None:
    _pause.set()


def resume_upload(client_job_id: str) -> None:
    _pause.clear()
    enqueue("upload", client_job_id)


# Worker ------------------------------------------------------------------------

def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    _stop.clear()
    _worker = threading.Thread(target=_run, name="clothnext-cloud", daemon=True)
    _worker.start()


def _progress(done: int, total: int) -> None:
    _publish(uploading=True, upload_done=done, upload_total=total)


def _perform(runtime: CloudJobs, action: str, client_job_id: str) -> str:
    if action == "upload":
        _publish(uploading=True, message="Uploading to cloud – keep Blender open.")
        try:
            record = runtime.upload_and_submit(
                client_job_id, cancelled=_pause.is_set, progress=_progress)
        finally:
            _publish(uploading=False)
        if record.state is LocalState.UNCONFIRMED:
            return "Possibly started – checking with server"
        if record.submitted_confirmed:
            return "Job confirmed. You can close Blender."
        return ""
    if action == "refresh":
        for record in runtime.registry.all():
            if record.submitted_confirmed and record.state in (
                    LocalState.QUEUED, LocalState.RUNNING):
                runtime.refresh(record.client_job_id)
        return ""
    if action == "recover":
        runtime.recover()
        return ""
    if action == "download":
        runtime.download(client_job_id)
        return "Cloud result downloaded"
    if action == "delete_result":
        # Confirmed by the operator dialog; only this action deletes it (E20).
        runtime.archive.delete_job(client_job_id, confirmed=True)
        runtime.registry.update(client_job_id, state=LocalState.SUCCEEDED,
                                result_folder="")
        return "Downloaded Cloud result deleted"
    if action == "cancel":
        runtime.cancel(client_job_id)
        return "Cloud job cancelled"
    raise ValueError(f"unknown Cloud action {action}")


def _run() -> None:
    while not _stop.is_set():
        try:
            action, client_job_id = _tasks.get(timeout=0.5)
        except queue.Empty:
            continue
        if action == "stop":
            break
        runtime = jobs()
        if runtime is None:
            _publish(message=service.PENDING_REASON, error_code="CNX-E201")
            continue
        try:
            message = _perform(runtime, action, client_job_id)
            _publish(jobs=runtime.registry.all(), message=message, error_code="")
        except Exception as exc:  # noqa: BLE001 - mapped to a trusted code below
            code = errors.code_of(exc)
            public, _info, text = errors.describe(
                code, request_id=getattr(exc, "request_id", ""))
            log_with_context(_LOG, 30, "Cloud action failed", {
                "action": action, "code": code, "cnx": public,
                "error_type": type(exc).__name__})
            try:
                records = runtime.registry.all()
            except Exception:  # noqa: BLE001 - keep the last view
                records = view().jobs
            _publish(jobs=records, message=text, error_code=public)


_main_thread_hooks: list = []


def tick() -> float:
    """Main-thread timer: periodic refresh requests; never blocks."""
    global _last_refresh
    for hook in tuple(_main_thread_hooks):
        try:
            hook()
        except Exception as exc:  # noqa: BLE001 - a hook never stops the timer
            log_with_context(_LOG, 30, "Cloud main-thread step failed",
                             {"error_type": type(exc).__name__})
    now = time.monotonic()
    if (service.availability().reason != service.PENDING_REASON
            and now - _last_refresh >= REFRESH_SECONDS):
        _last_refresh = now
        if any(record.submitted_confirmed and record.state in (
                LocalState.QUEUED, LocalState.RUNNING) for record in view().jobs):
            enqueue("refresh")
    return 1.0


def start() -> None:
    """After register: settle unclear jobs from an earlier session (E23.4)."""
    if service.availability().reason == service.PENDING_REASON:
        return
    enqueue("recover")


def shutdown(timeout: float = 5.0) -> bool:
    """Stop the worker; an interrupted upload resumes after restart."""
    global _worker
    _pause.set()
    _stop.set()
    _tasks.put(("stop", ""))
    worker = _worker
    if worker is not None and worker.is_alive():
        worker.join(timeout)
    stopped = worker is None or not worker.is_alive()
    _worker = None
    _pause.clear()
    return stopped


def describe_state(record: JobRecord) -> str:
    """Artist-facing status line for one job (concept 12.4)."""
    state = record.state
    if state in (LocalState.UPLOADING, LocalState.PACKAGED, LocalState.CREATE_REQUESTED):
        total = max(record.upload_bytes_total, 1)
        return f"Uploading {100 * record.upload_bytes_done // total} % – keep Blender open"
    if state is LocalState.UPLOAD_PAUSED:
        return "Upload paused – not sent"
    if state in (LocalState.UNCONFIRMED, LocalState.SUBMIT_REQUESTED):
        return "Possibly started – checking with server"
    if state is LocalState.QUEUED:
        return (f"Waiting (position {record.queue_position})"
                if record.queue_position else "Waiting")
    if state is LocalState.RUNNING:
        done = record.progress_frames or 0
        total = record.total_frames or 0
        return f"Simulating {done}/{total}" if total else "Simulating"
    if state is LocalState.SUCCEEDED:
        until = f" · available until {record.expires_at}" if record.expires_at else ""
        return "Ready on server" + until
    if state is LocalState.DOWNLOADING:
        return "Downloading"
    if state is LocalState.STORED:
        return "Stored"
    if state is LocalState.STOPPED_AT_LIMIT:
        return "Stopped at cost limit"
    if state is LocalState.FAILED:
        return f"Failed ({errors.cnx_code('solver_failed')})"
    if state is LocalState.CANCELLED:
        return "Cancelled"
    if state is LocalState.EXPIRED:
        return "Expired on server"
    if state is LocalState.DISCARDED:
        return "Discarded"
    return state.value.title()



def register() -> None:
    import bpy
    set_blender_version(getattr(bpy.app, "version_string", "unknown"))
    timers = getattr(bpy.app, "timers", None)
    if timers is not None and not timers.is_registered(tick):
        timers.register(tick, first_interval=1.0, persistent=True)
    start()


def unregister() -> None:
    import bpy
    timers = getattr(bpy.app, "timers", None)
    if timers is not None and timers.is_registered(tick):
        timers.unregister(tick)
    shutdown()
    reset()
