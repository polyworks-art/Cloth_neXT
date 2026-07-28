# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Runtime integration for reliable recovery and export reuse.

The large Bake service deliberately stays stable.  These hooks connect the
pure recovery/PC2 primitives to Blender while keeping the behavioral changes
small and independently reversible during add-on unregister/reload.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

import bpy

from ..bake import cache_metadata, pc2
from ..bake.status import BakeState
from ..core.logging import get_logger, log_with_context
from ..export_cache import deterministic_key
from ..ppf_run.session import SessionCancelled, SolverSession
from . import solver_test

_logger = get_logger("recovery.runtime")

_original_scene_source_key = None
_original_session_run = None
_original_writer_preserve = None
_original_discard_incomplete = None
_original_pump_once = None


def _target_plans(plan):
    targets = tuple(getattr(plan, "deformables", ()))
    if targets:
        return targets
    return (solver_test.DeformablePlan(
        plan.initial_local, plan.world_matrix, plan.cloth_object_name,
        str(getattr(plan.scene, "cloth_uuid", "legacy-cloth")),
        plan.pc2_path, plan.topology_signature, plan.material_meta,
        plan.deformable_role, plan.stitch_pairs,
        plan.stitch_snap_distance),)


def _canonical_scene_source_key(context, snapshot):
    """Evaluate the cheap export identity at Bake Start, not timeline current."""
    scene = context.scene
    original_frame = int(scene.frame_current)
    original_subframe = float(getattr(scene, "frame_subframe", 0.0))
    bake_start = int(snapshot.bake_range.start)
    try:
        scene.frame_set(bake_start, subframe=0.0)
        key, reason = _original_scene_source_key(context, snapshot)
    finally:
        scene.frame_set(original_frame, subframe=original_subframe)
    if not key:
        return key, reason
    return deterministic_key("scene", {
        "canonical_frame_schema": 1,
        "bake_start": bake_start,
        "source_key": key,
    }), f"{reason}; canonical Bake Start identity"


def _session_run_with_partial_preservation(self):
    try:
        return _original_session_run(self)
    except SessionCancelled as exc:
        # A solver checkpoint and a playable partial result are independent.
        # Any downloaded frame is already validated and can be kept even when
        # save_and_quit was too early or the server checkpoint failed.
        if self.diagnostics.fetched_frames and not exc.resumable:
            raise SessionCancelled(
                resumable=True,
                recovery_outcome=exc.recovery_outcome) from exc
        raise


def _publish_partial_preview(writer, partial_path: Path):
    frame_count = int(writer.frames_written)
    if frame_count <= 0:
        return None
    header = pc2.Pc2Header(
        writer.header.vertex_count, writer.header.start_frame,
        writer.header.sample_rate, frame_count)
    expected_size = (pc2.PC2_HEADER_SIZE
                     + frame_count * writer.header.vertex_count * 12)
    final_path = Path(writer.final_path)
    temporary = final_path.with_name(
        f".{final_path.name}.{uuid.uuid4().hex}.partial.tmp")
    backup = final_path.with_name(
        f".{final_path.name}.{uuid.uuid4().hex}.bak")
    try:
        with Path(partial_path).open("rb") as source, temporary.open("xb") as target:
            raw = source.read(pc2.PC2_HEADER_SIZE)
            if raw != pc2._header_bytes(writer.header):
                raise pc2.Pc2Error(
                    "partial PC2 header changed before preview publication")
            target.write(pc2._header_bytes(header))
            shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
            target.flush()
            if target.tell() != expected_size:
                raise pc2.Pc2Error(
                    "partial PC2 preview ended off a complete frame boundary")
            os.fsync(target.fileno())
        if final_path.exists():
            os.link(final_path, backup)
        os.replace(temporary, final_path)
        if pc2.read_header(final_path) != header:
            raise pc2.Pc2Error("published partial PC2 failed validation")
        backup.unlink(missing_ok=True)
        return header
    except Exception:
        temporary.unlink(missing_ok=True)
        if backup.exists():
            try:
                os.replace(backup, final_path)
            except OSError:
                pass
        raise


def _preserve_with_preview(writer):
    partial_path = _original_writer_preserve(writer)
    try:
        writer._clothnext_partial_preview_header = _publish_partial_preview(
            writer, partial_path)
    except (OSError, pc2.Pc2Error) as exc:
        # Resume data remains valid even when publishing the optional Blender
        # preview failed. Never turn a successful recovery save into data loss.
        writer._clothnext_partial_preview_header = None
        log_with_context(_logger, logging.WARNING,
                         "Partial playback preview could not be published", {
            "partial_path": str(partial_path),
            "final_path": str(writer.final_path),
            "reason": f"{type(exc).__name__}: {exc}",
        })
    return partial_path


def _partial_metadata(plan, target, header) -> dict | None:
    material_meta = getattr(target, "material_meta", None) or {}
    try:
        fingerprints = material_meta["fingerprints"]
        identities = material_meta["identities"]
        expected = dict(material_meta["expected"])
        details = dict(material_meta["details"])
    except (KeyError, TypeError, ValueError):
        return None
    expected.update({
        "vertex_count": header.vertex_count,
        "frame_count": header.frame_count,
        "start_frame": header.start_frame,
        "sample_rate": header.sample_rate,
    })
    details.update({
        "blender_end_frame": plan.frame_start + header.frame_count - 1,
        "partial_result": {
            "cached_frame_count": header.frame_count,
            "requested_frame_count": plan.frame_count,
            "cancelled": True,
        },
    })
    partial = cache_metadata.partial_metadata(
        cache_path=target.pc2_path,
        fingerprints=fingerprints,
        identities=identities,
        expected=expected,
        details=details)
    return cache_metadata.completed_metadata(
        partial, cache_path=target.pc2_path)


def _preview_headers(plan):
    headers = {}
    targets = _target_plans(plan)
    for target in targets:
        try:
            header = pc2.read_header(target.pc2_path)
        except (OSError, pc2.Pc2Error):
            return None
        if header.frame_count <= 0 or header.frame_count > plan.frame_count:
            return None
        headers[target.uuid] = header
    counts = {header.frame_count for header in headers.values()}
    if len(counts) != 1:
        return None
    return headers


def _retain_cancelled_preview(plan, *, state="failed", reason=""):
    if state != "cancelled":
        return _original_discard_incomplete(plan, state=state, reason=reason)
    headers = _preview_headers(plan)
    if not headers:
        return _original_discard_incomplete(plan, state=state, reason=reason)
    targets = _target_plans(plan)
    records = []
    for target in targets:
        metadata = _partial_metadata(plan, target, headers[target.uuid])
        if metadata is None:
            return _original_discard_incomplete(
                plan, state=state, reason=reason)
        records.append((target, metadata))
    try:
        for target, metadata in records:
            cache_metadata.write_atomic(
                cache_metadata.sidecar_path(target.pc2_path), metadata)
    except OSError:
        return _original_discard_incomplete(plan, state=state, reason=reason)
    log_with_context(_logger, logging.INFO,
                     "Cancelled Bake retained a playable partial cache", {
        "objects": len(targets),
        "frames": next(iter(headers.values())).frame_count,
    })


def _attach_cancelled_preview(plan) -> bool:
    headers = _preview_headers(plan)
    if not headers:
        return False
    frame_count = next(iter(headers.values())).frame_count
    partial_plan = replace(
        plan,
        frame_count=frame_count,
        frame_end=plan.frame_start + frame_count - 1)
    payload = (headers if getattr(plan, "deformables", ())
               else next(iter(headers.values())))
    solver_test._attach_playback(partial_plan, payload)
    snapshot = solver_test.shared_controller.snapshot()
    suffix = "frame" if frame_count == 1 else "frames"
    solver_test.shared_controller.update(
        status_message=f"Bake cancelled · {frame_count} {suffix} cached",
        current_frame=partial_plan.frame_end,
        progress_current=frame_count,
        progress_total=plan.frame_count,
        estimated_remaining_seconds=None)
    log_with_context(_logger, logging.INFO,
                     "Attached cancelled Bake progress", {
        "frames": frame_count,
        "requested_frames": plan.frame_count,
        "state": snapshot.state.value,
    })
    return True


def _pump_once_with_partial_attach():
    plan = solver_test._active_plan
    result = _original_pump_once()
    if plan is None or solver_test._active_plan is not None:
        return result
    state = solver_test.shared_controller.snapshot().state
    if state not in {BakeState.CANCELLED, BakeState.ERROR}:
        return result
    try:
        _attach_cancelled_preview(plan)
    except (OSError, ValueError, RuntimeError, pc2.Pc2Error) as exc:
        log_with_context(_logger, logging.WARNING,
                         "Cancelled Bake preview could not be attached", {
            "reason": f"{type(exc).__name__}: {exc}",
        })
    return result


def install_runtime_hooks() -> None:
    global _original_scene_source_key, _original_session_run
    global _original_writer_preserve, _original_discard_incomplete
    global _original_pump_once

    if not getattr(solver_test._scene_source_key,
                   "_clothnext_recovery_runtime", False):
        _original_scene_source_key = solver_test._scene_source_key
        _canonical_scene_source_key._clothnext_recovery_runtime = True
        solver_test._scene_source_key = _canonical_scene_source_key

    if not getattr(SolverSession.run, "_clothnext_recovery_runtime", False):
        _original_session_run = SolverSession.run
        _session_run_with_partial_preservation._clothnext_recovery_runtime = True
        SolverSession.run = _session_run_with_partial_preservation

    if not getattr(pc2.StreamingPc2Writer.preserve,
                   "_clothnext_recovery_runtime", False):
        _original_writer_preserve = pc2.StreamingPc2Writer.preserve
        _preserve_with_preview._clothnext_recovery_runtime = True
        pc2.StreamingPc2Writer.preserve = _preserve_with_preview

    if not getattr(solver_test._discard_incomplete,
                   "_clothnext_recovery_runtime", False):
        _original_discard_incomplete = solver_test._discard_incomplete
        _retain_cancelled_preview._clothnext_recovery_runtime = True
        solver_test._discard_incomplete = _retain_cancelled_preview

    if not getattr(solver_test._pump_once,
                   "_clothnext_recovery_runtime", False):
        _original_pump_once = solver_test._pump_once
        _pump_once_with_partial_attach._clothnext_recovery_runtime = True
        solver_test._pump_once = _pump_once_with_partial_attach


def uninstall_runtime_hooks() -> None:
    global _original_scene_source_key, _original_session_run
    global _original_writer_preserve, _original_discard_incomplete
    global _original_pump_once

    if (_original_scene_source_key is not None and getattr(
            solver_test._scene_source_key,
            "_clothnext_recovery_runtime", False)):
        solver_test._scene_source_key = _original_scene_source_key
    if (_original_session_run is not None and getattr(
            SolverSession.run, "_clothnext_recovery_runtime", False)):
        SolverSession.run = _original_session_run
    if (_original_writer_preserve is not None and getattr(
            pc2.StreamingPc2Writer.preserve,
            "_clothnext_recovery_runtime", False)):
        pc2.StreamingPc2Writer.preserve = _original_writer_preserve
    if (_original_discard_incomplete is not None and getattr(
            solver_test._discard_incomplete,
            "_clothnext_recovery_runtime", False)):
        solver_test._discard_incomplete = _original_discard_incomplete
    if (_original_pump_once is not None and getattr(
            solver_test._pump_once,
            "_clothnext_recovery_runtime", False)):
        solver_test._pump_once = _original_pump_once

    _original_scene_source_key = None
    _original_session_run = None
    _original_writer_preserve = None
    _original_discard_incomplete = None
    _original_pump_once = None
