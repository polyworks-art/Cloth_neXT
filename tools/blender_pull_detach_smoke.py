# SPDX-License-Identifier: GPL-3.0-or-later
"""Factory Blender registration/playback smoke; --events exercises real UI and undo."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender import floating_simulation as floating, physics_operators


def main():
    cloth_next.register()
    obj = bpy.context.active_object
    obj.cloth_next.enabled = True
    modifier = obj.modifiers.new("Baked playback", "MESH_CACHE")
    modifier.filepath = "/tmp/preserved.pc2"
    assert physics_operators.remove_physics_targets(bpy.context.scene, (obj,))
    assert modifier in tuple(obj.modifiers) and modifier.filepath == "/tmp/preserved.pc2"
    assert not obj.cloth_next.enabled
    cloth_next.unregister()
    assert not floating._pull_sessions and floating._handle is None
    cloth_next.register()
    if "--events" not in sys.argv:
        cloth_next.unregister()
        print("Pull detach registration and playback smoke passed")
        return
    addon = bpy.context.preferences.addons.get("cloth_next")
    if addon is None:
        addon = bpy.context.preferences.addons.new()
        addon.module = "cloth_next"
    addon.preferences.new_look = True
    floating.sync()
    window = bpy.context.window
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")

    def send(kind, value, point):
        window.event_simulate(type=kind, value=value, x=round(point[0]+region.x), y=round(point[1]+region.y))

    def sequence():
        send("ESC", "PRESS", (10, 10))
        yield .2
        send("ESC", "RELEASE", (10, 10))
        with bpy.context.temp_override(window=window, area=area, region=region):
            obj.cloth_next.enabled = True
            bpy.ops.ed.undo_push(message="Pull detach baseline")
            x, y, _, _, s = floating._animated_bounds(bpy.context)
        center = (x+27*s, y+27*s)
        target = (center[0]-160*s, center[1])
        for cancel in (True, False):
            send("MOUSEMOVE", "NOTHING", center)
            yield .2
            send("LEFTMOUSE", "PRESS", center)
            yield .2
            assert len(floating._pull_sessions) == 1
            send("MOUSEMOVE", "NOTHING", target)
            yield .2
            assert next(iter(floating._pull_sessions.values())).gesture.state == "ARMED"
            if cancel:
                send("MOUSEMOVE", "NOTHING", center)
                yield .2
            send("LEFTMOUSE", "RELEASE", center if cancel else target)
            yield .2
            assert not floating._pull_sessions
            assert bpy.context.active_object.cloth_next.enabled == cancel
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.ed.undo()
        yield .2
        assert bpy.context.active_object.cloth_next.enabled
        assert bpy.context.active_object.modifiers.get("Baked playback")
        cloth_next.unregister()
        print("Pull detach real press/arm/disarm/release/undo passed", flush=True)
        bpy.ops.wm.quit_blender()

    steps = sequence()
    def tick():
        try:
            return next(steps)
        except StopIteration:
            return None
        except Exception:
            import traceback, os
            traceback.print_exc()
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(1)
    bpy.app.timers.register(tick, first_interval=2.)


if __name__ == "__main__":
    main()
