# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with Blender --factory-startup --python; append -- --interactive for UI QA.

Does not invoke Bake or start a solver. Interactive mode leaves a disposable
factory scene open with New Look enabled; never saves user preferences/files.
"""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender import floating_simulation, quick_assign


def main():
    other = bpy.context.active_object.copy()
    other.data = other.data.copy()
    other.name = "Other selected mesh"
    bpy.context.collection.objects.link(other)
    other.location.x += 3
    other.select_set(True)
    for _ in range(3):
        cloth_next.register()
        cloth_next.register()
        obj = bpy.context.active_object
        obj.cloth_next.enabled = False
        for role in ("CLOTH", "RIGID_BODY", "SOFT_BODY", "COLLIDER"):
            assert bpy.ops.clothnext.quick_assign_role(role=role) == {"FINISHED"}
            assert obj.cloth_next.enabled and obj.cloth_next.role == role
            assert not other.cloth_next.enabled  # existing active-object semantics
        previous = obj.cloth_next.role
        assert bpy.ops.clothnext.quick_assign_role(role="ROD") == {"CANCELLED"}
        assert obj.cloth_next.role == previous
        cloth_next.unregister()
        cloth_next.unregister()
        assert not quick_assign._sessions
        assert floating_simulation._handle is None
        assert floating_simulation._scene_loading not in bpy.app.handlers.load_pre
        assert not hasattr(bpy.types.Object, "cloth_next")
    print("Quick Assign import, dispatch and repeated registration smoke passed")
    if "--interactive" in sys.argv or "--events" in sys.argv:
        cloth_next.register()
        addon = bpy.context.preferences.addons.get("cloth_next")
        if addon is None:
            addon = bpy.context.preferences.addons.new()
            addon.module = "cloth_next"
        addon.preferences.new_look = True
        floating_simulation.sync()
        bpy.context.active_object.cloth_next.enabled = False
        bpy.context.workspace.name = "Quick Assign QA"
        if "--events" in sys.argv:
            run_events()


def run_events():
    """Real window events, requires Blender's --enable-event-simulate flag."""
    import math
    import traceback
    window = bpy.context.window
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")

    def send(kind, value, point):
        window.event_simulate(type=kind, value=value,
                              x=round(point[0]+region.x), y=round(point[1]+region.y))

    def sequence():
        send("ESC", "PRESS", (10, 10))  # dismiss factory startup splash
        yield .2
        send("ESC", "RELEASE", (10, 10))
        yield .2
        with bpy.context.temp_override(window=window, area=area, region=region):
            center = quick_assign.button_bounds(bpy.context)[:2]
            bpy.ops.ed.undo_push(message="Quick Assign QA baseline")
        send("MOUSEMOVE", "NOTHING", center)
        yield .2
        if "--screenshot" in sys.argv:
            path = sys.argv[sys.argv.index("--screenshot")+1]
            bpy.ops.screen.screenshot(filepath=path)
        send("LEFTMOUSE", "PRESS", center)
        yield .3
        assert len(quick_assign._sessions) == 1, "Quick button did not capture press"
        op = next(iter(quick_assign._sessions.values()))
        assert op.gesture.state == "RADIAL_OPEN", op.gesture.state
        point = op.gesture.layout.point(2, 35*op.scale)
        assert math.dist(point, op.gesture.layout.point(2)) > op.gesture.layout.bubble_radius
        send("MOUSEMOVE", "NOTHING", point)
        yield .2
        assert op.gesture.target == "RIGID_BODY"
        if "--screenshot" in sys.argv:
            path = sys.argv[sys.argv.index("--screenshot")+1]
            bpy.ops.screen.screenshot(filepath=path)
        if "--screenshot" in sys.argv:
            for index, name in ((3, "soft-body"), (4, "collider")):
                send("MOUSEMOVE", "NOTHING", op.gesture.layout.point(index, 35*op.scale))
                yield .2
                assert op.gesture.target == op.gesture.layout.roles[index]
                snapshot = Path(path)
                bpy.ops.screen.screenshot(filepath=str(snapshot.with_stem(snapshot.stem+"-"+name)))
            send("MOUSEMOVE", "NOTHING", point)
            yield .2
        send("LEFTMOUSE", "RELEASE", point)
        yield .2
        assert not quick_assign._sessions
        assert not any(op.bl_idname == "CLOTHNEXT_OT_quick_assign"
                       for op in window.modal_operators)
        assert bpy.context.active_object.cloth_next.role == "RIGID_BODY"
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.ed.undo()
        yield .2
        assert not bpy.context.active_object.cloth_next.enabled, "Assignment needs more than one undo"
        for cancel in ("CENTER", "OUTSIDE", "ESC", "RIGHTMOUSE"):
            send("MOUSEMOVE", "NOTHING", center)
            yield .1
            send("LEFTMOUSE", "PRESS", center)
            yield .25
            op = next(iter(quick_assign._sessions.values()))
            target = op.gesture.layout.point(0, 35*op.scale)
            send("MOUSEMOVE", "NOTHING", target)
            yield .1
            if cancel in {"CENTER", "OUTSIDE"}:
                target = center if cancel == "CENTER" else (center[0], center[1]+200*op.scale)
                send("MOUSEMOVE", "NOTHING", target)
                yield .1
                send("LEFTMOUSE", "RELEASE", target)
            else:
                send(cancel, "PRESS", target)
                yield .1
                send("LEFTMOUSE", "RELEASE", target)
            yield .1
            assert not quick_assign._sessions, cancel
            assert not any(op.bl_idname == "CLOTHNEXT_OT_quick_assign"
                           for op in window.modal_operators), cancel
            assert not bpy.context.active_object.cloth_next.enabled, cancel
        # Short clicks and repeated gestures never leave a modal handler.
        for _ in range(6):
            send("MOUSEMOVE", "NOTHING", center)
            yield .03
            send("LEFTMOUSE", "PRESS", center)
            yield .03
            send("LEFTMOUSE", "RELEASE", center)
            yield .03
            assert not quick_assign._sessions
        # Disabling while a gesture is active must tear down runtime state.
        send("MOUSEMOVE", "NOTHING", center)
        yield .1
        send("LEFTMOUSE", "PRESS", center)
        yield .25
        assert quick_assign._sessions
        cloth_next.unregister()
        send("MOUSEMOVE", "NOTHING", center)
        yield .2
        assert not quick_assign._sessions and floating_simulation._handle is None
        assert not any(op.bl_idname == "CLOTHNEXT_OT_quick_assign"
                       for op in window.modal_operators)
        cloth_next.register()
        print("Quick Assign live press/hold/sector/release/undo/cancel checks passed", flush=True)
        cloth_next.unregister()
        bpy.ops.wm.quit_blender()

    steps = sequence()

    def tick():
        try:
            return next(steps)
        except StopIteration:
            return None
        except Exception:
            traceback.print_exc()
            # Timer exceptions otherwise leave Blender running and return a
            # successful process code. This is a disposable test process.
            import os
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(1)

    bpy.app.timers.register(tick, first_interval=2.)


if __name__ == "__main__":
    main()
