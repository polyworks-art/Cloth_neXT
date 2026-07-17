# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""User-facing beta readiness tools; all Blender access stays on main thread."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import platform
import shutil

import bpy

from .. import manifest_version
from ..bake.controller import shared_controller
from ..core.beta_readiness import (
    CacheEntry, HealthCheck, HealthSeverity, collider_capture_bytes,
    human_bytes, inventory_cache, pc2_size_bytes, redact_text,
    remove_invalid, support_markdown)
from ..telemetry import shared_telemetry
from . import solver_test


_last_health: tuple[HealthCheck, ...] = ()
_last_cache: tuple[CacheEntry, ...] = ()
_last_cache_root: Path | None = None
_last_support_report: Path | None = None


def cache_root(context) -> Path:
    obj = getattr(context, "object", None) or getattr(context, "active_object", None)
    settings = getattr(obj, "cloth_next", None) if obj is not None else None
    configured = str(getattr(settings, "cache_directory", "") or "").strip()
    return (Path(bpy.path.abspath(configured)) if configured
            else solver_test._cache_directory()).expanduser().resolve()


def _enabled(context, roles):
    return tuple(obj for obj in getattr(context.scene, "objects", ())
                 if getattr(getattr(obj, "cloth_next", None), "enabled", False)
                 and str(obj.cloth_next.role) in roles)


def _vertex_count(obj) -> int:
    data = getattr(obj, "data", None)
    vertices = getattr(data, "vertices", None)
    if vertices is not None:
        return len(vertices)
    return sum(len(getattr(spline, "bezier_points", ()))
               if str(getattr(spline, "type", "")) == "BEZIER"
               else len(getattr(spline, "points", ()))
               for spline in getattr(data, "splines", ()))


def run_health_checks(context) -> tuple[HealthCheck, ...]:
    checks = []
    try:
        snapshot = solver_test.validate_scene(context)
        checks.append(HealthCheck("scene", HealthSeverity.PASS,
            "Scene validation", "Geometry, materials, ranges and solver setup are valid."))
    except Exception as exc:  # noqa: BLE001 -- presented as actionable preflight
        return (HealthCheck("scene", HealthSeverity.ERROR, "Scene validation",
            str(exc), "Correct this error before Bake."),)
    deformables = _enabled(context, {"CLOTH", "ROD", "SOFT_BODY"})
    colliders = _enabled(context, {"COLLIDER"})
    frame_count = snapshot.bake_range.output_count
    vertices = sum(_vertex_count(obj) for obj in deformables)
    cache_estimate = sum(pc2_size_bytes(_vertex_count(obj), frame_count)
                         for obj in deformables)
    collider_estimate = 0
    low_sampling = []
    for obj in colliders:
        if str(getattr(obj.cloth_next, "collider_motion", "STATIC")) != "ANIMATED":
            continue
        samples = int(getattr(obj.cloth_next, "collider_samples_per_frame", 8))
        collider_estimate += collider_capture_bytes(
            _vertex_count(obj),
            frame_count, samples)
        if samples < 8:
            low_sampling.append(obj.name)
    root = cache_root(context)
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
        required = cache_estimate + collider_estimate
        severity = (HealthSeverity.ERROR if required > free
                    else HealthSeverity.WARNING if required > free * 0.5
                    else HealthSeverity.PASS)
        checks.append(HealthCheck("storage", severity, "Cache storage",
            f"Estimated {human_bytes(required)}; {human_bytes(free)} free.",
            "Choose a larger cache drive." if severity is not HealthSeverity.PASS else ""))
    except OSError as exc:
        checks.append(HealthCheck("storage", HealthSeverity.ERROR,
            "Cache storage", str(exc), "Choose an accessible cache directory."))
    if low_sampling:
        checks.append(HealthCheck("collider_sampling", HealthSeverity.WARNING,
            "Animated Collider sampling",
            f"{len(low_sampling)} collider(s) use fewer than 8 samples per frame.",
            "Use 8–16 samples for fast or curved motion."))
    elif colliders:
        checks.append(HealthCheck("collider_sampling", HealthSeverity.PASS,
            "Animated Collider sampling", "Sampling is at the recommended baseline."))
    telemetry = shared_telemetry.snapshot()
    ram_used = getattr(telemetry, "ram_used_bytes", None)
    ram_total = getattr(telemetry, "ram_total_bytes", None)
    ram_percent = (100.0 * ram_used / ram_total
                   if ram_used is not None and ram_total else 0.0)
    checks.append(HealthCheck("ram", (HealthSeverity.WARNING if ram_percent >= 80
                                      else HealthSeverity.PASS), "System RAM",
        f"Current system usage: {ram_percent:.0f}%.",
        "Close other heavy applications before Bake." if ram_percent >= 80 else ""))
    checks.append(HealthCheck("scope", HealthSeverity.PASS, "Bake scope",
        f"{len(deformables)} deformable(s), {len(colliders)} collider(s), "
        f"{frame_count} frame(s), {vertices:,} source vertices."))
    return tuple(checks)


def _report_path(context) -> Path:
    root = cache_root(context) / "support-reports"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return root / f"cloth-next-support-{stamp}.md"


def _latest_cache_diagnostics(root: Path) -> dict:
    sidecars = sorted(Path(root).glob("cn_test_cloth_*.meta.json"),
                      key=lambda path: path.stat().st_mtime, reverse=True)
    for sidecar in sidecars:
        try:
            if sidecar.stat().st_size > 2 * 1024 * 1024:
                continue
            value = json.loads(sidecar.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("completion_state") != "complete":
                continue
            identities = value.get("identities", {})
            solver = identities.get("solver", {}) if isinstance(identities, dict) else {}
            details = value.get("details", {})
            contacts = details.get("contacts", {}) if isinstance(details, dict) else {}
            return {"solver": solver if isinstance(solver, dict) else {},
                    "contacts": contacts if isinstance(contacts, dict) else {}}
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    return {"solver": {}, "contacts": {}}


def write_support_report(context) -> Path:
    snapshot = shared_controller.snapshot()
    telemetry = shared_telemetry.snapshot()
    objects = tuple(getattr(context.scene, "objects", ()))
    replacements = {str(Path.home()): "<HOME>", str(Path(bpy.app.tempdir)): "<TEMP>"}
    replacements.update({str(getattr(obj, "name", "")): f"<OBJECT-{index + 1}>"
                         for index, obj in enumerate(objects)})
    error_summary = redact_text(snapshot.error_summary, replacements)
    error_details = redact_text(snapshot.error_details, replacements)
    cached = _latest_cache_diagnostics(cache_root(context))
    solver = cached["solver"]
    contacts = cached["contacts"]
    sections = (
        ("Versions", (("Cloth NeXt", manifest_version()),
                      ("Blender", getattr(bpy.app, "version_string", "unknown")),
                      ("Operating system", platform.platform()),
                      ("Python", platform.python_version()))),
        ("Bake", (("State", snapshot.state.value),
                  ("Error code", snapshot.error_code or "none"),
                  ("Summary", error_summary or "none"),
                  ("Details", error_details or "none"))),
        ("Solver", (("Mode", snapshot.solver_mode or solver.get("mode", "unknown")),
                    ("Version", snapshot.solver_version or
                     solver.get("package_version", "unknown")),
                    ("Contact last", contacts.get("last", "unavailable")),
                    ("Contact peak", contacts.get("peak", "unavailable")),
                    ("Contact samples", contacts.get("samples", "unavailable")))),
        ("Resources", (("CPU", f"{float(getattr(telemetry, 'cpu_utilization_percent', 0) or 0):.0f}%"),
                       ("RAM", f"{(100.0 * telemetry.ram_used_bytes / telemetry.ram_total_bytes if telemetry.ram_used_bytes is not None and telemetry.ram_total_bytes else 0):.0f}%"),
                       ("GPU", redact_text(getattr(
                           getattr(telemetry, "primary_gpu", None), "name", "unavailable"),
                           replacements)))),
        ("Scene statistics", (("Objects", len(objects)),
                              ("Enabled deformables", len(_enabled(
                                  context, {"CLOTH", "ROD", "SOFT_BODY"}))),
                              ("Enabled colliders", len(_enabled(context, {"COLLIDER"}))),
                              ("Enabled forces", len(_enabled(context, {"FORCE"}))))),
        ("Privacy", (("Geometry included", "no"),
                     ("Object names", "redacted"),
                     ("Filesystem paths", "redacted"))))
    path = _report_path(context)
    path.write_text(support_markdown(sections), encoding="utf-8", newline="\n")
    return path


class CLOTHNEXT_OT_scene_health(bpy.types.Operator):
    bl_idname = "clothnext.scene_health"
    bl_label = "Run Scene Health Check"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, _context):
        return not shared_controller.snapshot().active

    def execute(self, context):
        global _last_health
        _last_health = run_health_checks(context)
        errors = sum(check.severity is HealthSeverity.ERROR for check in _last_health)
        warnings = sum(check.severity is HealthSeverity.WARNING for check in _last_health)
        self.report({"ERROR" if errors else "WARNING" if warnings else "INFO"},
                    f"Scene Health: {errors} error(s), {warnings} warning(s).")
        return {"CANCELLED" if errors else "FINISHED"}


class CLOTHNEXT_OT_export_support_report(bpy.types.Operator):
    bl_idname = "clothnext.export_support_report"
    bl_label = "Export Support Report"
    bl_options = {"REGISTER"}

    def execute(self, context):
        global _last_support_report
        try:
            _last_support_report = write_support_report(context)
        except OSError as exc:
            self.report({"ERROR"}, f"Support report could not be written: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Support report written: {_last_support_report}")
        return {"FINISHED"}


class CLOTHNEXT_OT_cache_scan(bpy.types.Operator):
    bl_idname = "clothnext.cache_scan"
    bl_label = "Scan Cloth NeXt Caches"
    bl_options = {"REGISTER"}

    def execute(self, context):
        global _last_cache, _last_cache_root
        _last_cache_root = cache_root(context)
        _last_cache = inventory_cache(_last_cache_root)
        self.report({"INFO"}, f"Found {len(_last_cache)} Cloth NeXt cache(s).")
        return {"FINISHED"}


class CLOTHNEXT_OT_cache_clear_invalid(bpy.types.Operator):
    bl_idname = "clothnext.cache_clear_invalid"
    bl_label = "Remove Invalid Caches"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, _context):
        return bool(_last_cache_root and any(entry.deletable for entry in _last_cache)
                    and not shared_controller.snapshot().active)

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        global _last_cache
        try:
            removed = remove_invalid(_last_cache, _last_cache_root)
            _last_cache = inventory_cache(_last_cache_root)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Removed {len(removed)} invalid cache file(s).")
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_scene_health, CLOTHNEXT_OT_export_support_report,
           CLOTHNEXT_OT_cache_scan, CLOTHNEXT_OT_cache_clear_invalid)
