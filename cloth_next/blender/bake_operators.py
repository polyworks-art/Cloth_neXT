# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
import bpy
from ..bake.controller import shared_controller
from . import bake_preview, companion_manager

class CLOTHNEXT_OT_preview_start(bpy.types.Operator):
    bl_idname="clothnext.preview_start"; bl_label="Start UI Preview"
    def execute(self, context):
        bake_preview.start(getattr(getattr(context, "object", None), "name", "")); return {"FINISHED"}

class CLOTHNEXT_OT_preview_cancel(bpy.types.Operator):
    bl_idname="clothnext.preview_cancel"; bl_label="Cancel UI Preview"
    def execute(self, _context):
        if shared_controller.snapshot().can_cancel: shared_controller.request_cancel()
        return {"FINISHED"}

class CLOTHNEXT_OT_preview_error(bpy.types.Operator):
    bl_idname="clothnext.preview_error"; bl_label="Trigger Preview Error"
    def execute(self, _context):
        shared_controller.fail("UI preview error", "Synthetic display test; PPF was not run."); return {"FINISHED"}

class CLOTHNEXT_OT_companion_launch(bpy.types.Operator):
    bl_idname="clothnext.companion_launch"; bl_label="Launch Bake Window"
    def execute(self,_context):
        ok,message=companion_manager.launch(); self.report({"INFO" if ok else "WARNING"},message)
        return {"FINISHED" if ok else "CANCELLED"}

class CLOTHNEXT_OT_companion_close(bpy.types.Operator):
    bl_idname="clothnext.companion_close"; bl_label="Close Bake Window"
    def execute(self,_context): companion_manager.shutdown(); return {"FINISHED"}

CLASSES=(CLOTHNEXT_OT_preview_start, CLOTHNEXT_OT_preview_cancel, CLOTHNEXT_OT_preview_error,
         CLOTHNEXT_OT_companion_launch,CLOTHNEXT_OT_companion_close)
