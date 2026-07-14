# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Session-scoped validation status for Cloth NeXt objects.

Why this module exists
----------------------
Blender calls ``Panel.draw()`` on every mouse move over the Properties
editor, on every timeline step, and on every depsgraph update. Any mesh work
performed there is paid for again and again. Cloth NeXt therefore validates
meshes *once*, records the outcome here, and lets the panels render that
recorded outcome.

What lives here is deliberately cheap and disposable:

* **No Blender data is retained.** Records are keyed by the object's
  ``name_full`` string, so nothing in this cache can keep an ``Object`` or a
  ``Mesh`` alive, survive a file load, or dangle after an undo.
* **Nothing is persisted.** A fresh session starts at ``UNKNOWN``, which
  reports honestly as "validation required" rather than falsely claiming a
  cache is safe.
* **Marking is O(1).** The depsgraph handler and the property update
  callbacks only flip a flag; they never read a vertex.

The expensive counterpart — the real topology hash and pin scan — lives in
:mod:`cloth_next.blender.solver_test` and installs itself here through
:func:`set_validator`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from enum import Enum

import bpy


class ValidationState(str, Enum):
    UNKNOWN = "UNKNOWN"      # never validated this session
    VALID = "VALID"          # fully validated, geometry unchanged since
    DIRTY = "DIRTY"          # something changed; must be re-validated
    INVALID = "INVALID"      # last full validation rejected the scene
    VALIDATING = "VALIDATING"  # a full validation is in flight


@dataclass(frozen=True, slots=True)
class ValidationRecord:
    """Everything a Panel.draw() may show — and nothing it must compute."""

    state: ValidationState = ValidationState.UNKNOWN
    pin_count: int = 0
    pin_group: str = ""
    object_key: str = ""
    mesh_key: str = ""
    topology_signature: str = ""
    geometry_fingerprint: str = ""
    settings_fingerprint: str = ""
    message: str = ""
    validated_at: float = 0.0
    settings_dirty: bool = False
    geometry_dirty: bool = False


_UNKNOWN = ValidationRecord()

# object_key -> ValidationRecord. Plain strings in, plain records out.
_records: dict[str, ValidationRecord] = {}

# Debounce window before the optional background validation runs (Phase 11).
VALIDATION_DEBOUNCE_SECONDS = 0.6
# Redraw tagging is throttled; dirty marking itself never is (see below).
_REDRAW_THROTTLE_SECONDS = 0.2

_validator = None
_auto_validate = True
_last_redraw_tag = 0.0
_validation_due = 0.0
_handlers_registered = False


# ---------------------------------------------------------------------------
# Identity: strings only, never a Blender reference.

def object_key(obj) -> str:
    """Stable, weak identity for an object: its full (library-aware) name."""
    if obj is None:
        return ""
    return str(getattr(obj, "name_full", None) or getattr(obj, "name", "") or "")


def mesh_key(obj) -> str:
    data = getattr(obj, "data", None)
    if data is None:
        return ""
    return str(getattr(data, "name_full", None) or getattr(data, "name", "") or "")


# ---------------------------------------------------------------------------
# Reading (safe from draw)

def record_for(obj) -> ValidationRecord:
    """The recorded status of ``obj``. Never touches mesh data."""
    key = object_key(obj)
    if not key:
        return _UNKNOWN
    record = _records.get(key)
    if record is None:
        return _UNKNOWN
    # A swapped mesh datablock invalidates a record that was taken on the old
    # one, even if no depsgraph update was seen for it.
    if record.mesh_key and record.mesh_key != mesh_key(obj):
        return replace(record, state=ValidationState.DIRTY, geometry_dirty=True)
    return record


def is_validated(obj) -> bool:
    return record_for(obj).state is ValidationState.VALID


# ---------------------------------------------------------------------------
# Writing (cheap; safe from update callbacks and handlers)

def _store(key: str, record: ValidationRecord) -> None:
    _records[key] = record


def _demote(obj, *, settings: bool = False, geometry: bool = False) -> bool:
    """Flip an object to DIRTY. Returns True when something actually changed."""
    key = object_key(obj)
    if not key:
        return False
    current = _records.get(key, _UNKNOWN)
    already = (current.state is ValidationState.DIRTY
               and current.settings_dirty >= settings
               and current.geometry_dirty >= geometry)
    if already:
        return False
    _store(key, replace(
        current,
        state=ValidationState.DIRTY,
        object_key=key,
        settings_dirty=current.settings_dirty or settings,
        geometry_dirty=current.geometry_dirty or geometry,
        # The last known pin count stays visible; it is labelled as stale by
        # the DIRTY state rather than being thrown away.
        message=""))
    return True


def mark_settings_dirty(obj) -> None:
    """A mapped property changed. No mesh is read."""
    if _demote(obj, settings=True):
        _schedule_validation()


def mark_geometry_dirty(obj) -> None:
    """The mesh may have changed. No mesh is read."""
    if _demote(obj, geometry=True):
        _schedule_validation()


def mark_all_settings_dirty() -> None:
    """A scene-wide value (solver quality) changed: every record is suspect."""
    touched = False
    for key, record in list(_records.items()):
        if record.state is ValidationState.DIRTY and record.settings_dirty:
            continue
        _records[key] = replace(record, state=ValidationState.DIRTY,
                                settings_dirty=True, message="")
        touched = True
    if touched:
        _schedule_validation()


def mark_validating(obj) -> None:
    key = object_key(obj)
    if not key:
        return
    _store(key, replace(_records.get(key, _UNKNOWN),
                        state=ValidationState.VALIDATING, object_key=key))


def store_valid(obj, *, pin_count: int, pin_group: str,
                topology_signature: str, geometry_fingerprint: str,
                settings_fingerprint: str) -> ValidationRecord:
    """Record the outcome of a *complete* validation."""
    key = object_key(obj)
    record = ValidationRecord(
        state=ValidationState.VALID, pin_count=int(pin_count),
        pin_group=str(pin_group), object_key=key, mesh_key=mesh_key(obj),
        topology_signature=topology_signature,
        geometry_fingerprint=geometry_fingerprint,
        settings_fingerprint=settings_fingerprint,
        validated_at=time.monotonic(),
        settings_dirty=False, geometry_dirty=False)
    if key:
        _store(key, record)
    return record


def store_invalid(obj, message: str) -> ValidationRecord:
    """Record a validation failure with the message the artist should read."""
    key = object_key(obj)
    current = _records.get(key, _UNKNOWN)
    record = replace(current, state=ValidationState.INVALID, object_key=key,
                     mesh_key=mesh_key(obj), message=str(message),
                     validated_at=time.monotonic())
    if key:
        _store(key, record)
    return record


# ---------------------------------------------------------------------------
# Lifecycle

def clear() -> None:
    """Drop every record. Used on register, unregister, and file load."""
    global _validation_due
    _records.clear()
    _validation_due = 0.0


def forget(obj) -> None:
    _records.pop(object_key(obj), None)


def prune(existing_keys) -> None:
    """Drop records for objects that no longer exist (deletion, undo)."""
    keep = set(existing_keys)
    for key in [key for key in _records if key not in keep]:
        del _records[key]


def set_validator(callback) -> None:
    """Install the expensive full-validation entry point (solver_test)."""
    global _validator
    _validator = callback


def set_auto_validate(enabled: bool) -> None:
    """Phase-11 background validation switch. Bake never depends on it."""
    global _auto_validate
    _auto_validate = bool(enabled)


# ---------------------------------------------------------------------------
# Cheap scene helpers used by the UI

def cloth_next_objects(scene):
    for obj in getattr(scene, "objects", ()):
        settings = getattr(obj, "cloth_next", None)
        if settings is not None and getattr(settings, "enabled", False):
            yield obj


# ---------------------------------------------------------------------------
# Depsgraph handler: marks dirty, reads nothing.

def _tag_redraw() -> None:
    """Throttled Properties redraw request. Never blocks and never validates."""
    global _last_redraw_tag
    now = time.monotonic()
    if now - _last_redraw_tag < _REDRAW_THROTTLE_SECONDS:
        return
    _last_redraw_tag = now
    windows = getattr(getattr(bpy.context, "window_manager", None), "windows", ())
    for window in windows:
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if getattr(area, "type", "") == "PROPERTIES":
                area.tag_redraw()


def _schedule_validation() -> None:
    """Arm the debounced validation timer (Phase 11, optional by design)."""
    global _validation_due
    if not _auto_validate or _validator is None:
        return
    _validation_due = time.monotonic() + VALIDATION_DEBOUNCE_SECONDS
    try:
        if not bpy.app.timers.is_registered(_validation_pump):
            bpy.app.timers.register(_validation_pump,
                                    first_interval=VALIDATION_DEBOUNCE_SECONDS)
    except (AttributeError, ValueError):
        pass


def _validation_pump():
    """Main-thread, debounced, one object per tick; aborts on new edits.

    Returning a delay reschedules; returning None retires the timer. The Bake
    path never waits for this — it is a convenience that upgrades UNKNOWN and
    DIRTY records to VALID while the user is idle.
    """
    if not _auto_validate or _validator is None:
        return None
    remaining = _validation_due - time.monotonic()
    if remaining > 0:
        return remaining  # a newer edit pushed the deadline out; re-arm
    try:
        pending = _validator()
    except Exception:  # noqa: BLE001 — a broken scene must not kill the timer
        return None
    if pending:
        _tag_redraw()
    return None


def _on_depsgraph_update(scene, depsgraph=None) -> None:
    """Mark Cloth NeXt objects dirty. Reads no vertices, edges, or polygons.

    Dirty marking itself is intentionally *not* throttled: skipping a mark
    could leave a changed mesh looking VALID, which is exactly the unsafe
    claim this refactor exists to prevent. It stays cheap instead — a demote
    is a dict write, and an object that is already DIRTY short-circuits.
    """
    updates = getattr(depsgraph, "updates", None) if depsgraph is not None else None
    if updates is None:
        return
    touched = False
    for update in updates:
        identifier = getattr(update, "id", None)
        if identifier is None:
            continue
        settings = getattr(identifier, "cloth_next", None)
        if settings is None or not getattr(settings, "enabled", False):
            continue
        if not (getattr(update, "is_updated_geometry", False)
                or getattr(update, "is_updated_transform", False)):
            continue
        # `identifier` is an evaluated copy; key by name, never by reference.
        if _demote(identifier, geometry=True):
            touched = True
    if touched:
        _tag_redraw()
        _schedule_validation()


def _on_load_post(*_args) -> None:
    clear()


def _on_undo_redo_post(scene, *_args) -> None:
    """Undo/redo can resurrect or remove objects; keep only what still exists."""
    try:
        prune(obj.name_full for obj in bpy.data.objects)
    except (AttributeError, TypeError):
        clear()


for _handler in (_on_depsgraph_update, _on_load_post, _on_undo_redo_post):
    _handler._clothnext_validation_handler = True


def _purge_stale(container) -> None:
    """Remove callbacks left behind by a previous module instance (reload)."""
    live = {_on_depsgraph_update, _on_load_post, _on_undo_redo_post}
    for func in list(container):
        if (getattr(func, "_clothnext_validation_handler", False)
                and func not in live):
            container.remove(func)


_HANDLER_SLOTS = (("depsgraph_update_post", "_on_depsgraph_update"),
                  ("load_post", "_on_load_post"),
                  ("undo_post", "_on_undo_redo_post"),
                  ("redo_post", "_on_undo_redo_post"))


def register() -> None:
    """Attach the handlers exactly once, even after a botched reload."""
    global _handlers_registered
    if _handlers_registered:
        return
    clear()
    handlers = getattr(bpy.app, "handlers", None)
    if handlers is None:  # pragma: no cover - defensive
        return
    for slot, attribute in _HANDLER_SLOTS:
        container = getattr(handlers, slot, None)
        if container is None:
            continue
        _purge_stale(container)
        callback = globals()[attribute]
        if callback not in container:
            container.append(callback)
    _handlers_registered = True


def unregister() -> None:
    global _handlers_registered
    handlers = getattr(bpy.app, "handlers", None)
    if handlers is not None:
        for slot, attribute in _HANDLER_SLOTS:
            container = getattr(handlers, slot, None)
            if container is None:
                continue
            callback = globals()[attribute]
            while callback in container:
                container.remove(callback)
            _purge_stale(container)
    try:
        if bpy.app.timers.is_registered(_validation_pump):
            bpy.app.timers.unregister(_validation_pump)
    except (AttributeError, ValueError):
        pass
    clear()
    _handlers_registered = False


def handler_count() -> int:
    """Diagnostic: how many Cloth NeXt handlers are currently attached."""
    handlers = getattr(bpy.app, "handlers", None)
    if handlers is None:
        return 0
    total = 0
    for slot, _attribute in _HANDLER_SLOTS:
        container = getattr(handlers, slot, ())
        total += sum(1 for func in container
                     if getattr(func, "_clothnext_validation_handler", False))
    return total
