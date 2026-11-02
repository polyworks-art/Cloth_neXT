# SPDX-License-Identifier: GPL-3.0-or-later
"""PATTERN NeXt: the integrated 2D pattern authoring workspace of Cloth NeXt.

Modules: ``properties`` (persistent RNA data), ``model`` (RNA <-> pure
geometry), ``sync`` (meshing and the generated Cloth NeXt panels, seams ->
Cloth NeXt Sewing), ``workspace`` (the native workspace tab and the locked
2D view), ``draw``/``toolbar`` (2D editor drawing), ``editor`` (the modal
2D editor session), ``operators`` and ``ui``. The geometry core is the
Blender-free package ``cloth_next.pattern_next``.

Registered through ``cloth_next.blender.registration`` like every other
feature: classes first, then :func:`register`; :func:`unregister` reverts
everything in reverse order and leaves no handler, timer or draw callback.
"""

from __future__ import annotations

import bpy

from .. import validation_state
from . import draw, editor, model, operators, properties, sync, ui, workspace

CLASSES = (properties.CLASSES + workspace.CLASSES + editor.CLASSES + operators.CLASSES
           + ui.CLASSES)


@validation_state.persistent
def _on_load_post(*_args):
    sync.clear_cache()
    draw.clear_cache()
    for scene in bpy.data.scenes:
        if hasattr(scene, "cloth_next_patterns"):
            model.migrate(scene)
    workspace.on_load_post()


@validation_state.persistent
def _on_undo(*_args):
    draw.clear_cache()
    editor.reset_transient()


# One load handler and one undo/redo handler for the whole feature.
_on_load_post._clothnext_pattern_handler = True
_on_undo._clothnext_pattern_handler = True


_HANDLERS = (("load_post", _on_load_post), ("undo_post", _on_undo), ("redo_post", _on_undo))


def register():
    properties.attach()
    for name, function in _HANDLERS:
        rows = getattr(bpy.app.handlers, name)
        if function not in rows:
            rows.append(function)
    draw.register()
    workspace.register()
    editor.register()
    ui.register()


def unregister():
    ui.unregister()
    editor.unregister()
    workspace.unregister()
    draw.unregister()
    for name, function in _HANDLERS:
        rows = getattr(bpy.app.handlers, name)
        if function in rows:
            rows.remove(function)
    sync.clear_cache()
    properties.detach()
