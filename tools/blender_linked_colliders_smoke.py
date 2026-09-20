# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared settings/save-load smoke; --events checks selection-free GPU picking."""
import sys
import math
import tempfile
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender import linked_colliders as linked, object_properties


def setup():
    cloth_next.register()
    source = bpy.context.active_object
    source.name = "Source"
    source.cloth_next.enabled = True
    source.cloth_next.role = "COLLIDER"
    others = []
    for name, x in (("Same", 3), ("Other", -3), ("Other partner", -6), ("Available", 6), ("Normal", 9)):
        obj = bpy.data.objects.new(name, source.data.copy())
        bpy.context.collection.objects.link(obj)
        obj.location.x = x
        obj.cloth_next.enabled = name != "Normal"
        obj.cloth_next.role = "COLLIDER"
        others.append(obj)
    source.cloth_next.collision.surface_grip = .3
    same, other, partner, available, normal = others
    assert linked.link(bpy.context.scene, source, same)
    assert linked.link(bpy.context.scene, other, partner)
    group = linked.group_for(source)
    group.collider_motion = "ANIMATED"
    group.surface_grip = .77
    assert math.isclose(object_properties.static_settings_from(same.cloth_next).surface_grip, .77, abs_tol=1e-6)
    assert object_properties.collider_motion_from(same.cloth_next) == "ANIMATED"
    assert not linked.link(bpy.context.scene, source, other)
    assert linked.link(bpy.context.scene, source, other, allow_move=True)
    assert not partner.cloth_next.collision_settings_group_id
    assert linked.unlink(bpy.context.scene, other)
    assert math.isclose(other.cloth_next.collision.surface_grip, .77, abs_tol=1e-6)
    assert linked.link(bpy.context.scene, other, partner)
    return source, same, other, available, normal


def main():
    source, same, other, available, normal = setup()
    if "--events" not in sys.argv:
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/"linked.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            source, same = bpy.data.objects["Source"], bpy.data.objects["Same"]
            assert linked.group_for(source) is not None
            assert linked.group_for(source).uuid == linked.group_for(same).uuid
            assert math.isclose(object_properties.static_settings_from(same.cloth_next).surface_grip, .77, abs_tol=1e-6)
            linked.repair_scene(bpy.context.scene)
        cloth_next.unregister()
        assert not linked._sessions and linked._handle is None and linked._feedback_handle is None
        print("Linked Colliders sharing, export, move, unlink, save/load and cleanup passed")
        return
    from bpy_extras import view3d_utils
    from mathutils import Quaternion, Vector
    window = bpy.context.window
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    if "--screenshot" in sys.argv:
        properties = next(a for a in window.screen.areas if a.type == "PROPERTIES")
        properties.spaces.active.context = "PHYSICS"
    rv3d = area.spaces.active.region_3d
    rv3d.view_rotation = Quaternion((1, 0, 0, 0))
    rv3d.view_distance = 25
    rv3d.view_location = Vector((1.5, 0, 0))
    rv3d.view_perspective = "ORTHO"
    selection = tuple(obj.name for obj in bpy.context.selected_objects)
    source_name = source.name

    def point(obj):
        location = view3d_utils.location_3d_to_region_2d(region, rv3d, obj.matrix_world.translation)
        assert location is not None
        return location

    def send(kind, value, location):
        window.event_simulate(type=kind, value=value, x=round(location[0]+region.x), y=round(location[1]+region.y))

    def preserved():
        assert bpy.context.active_object.name == source_name
        assert tuple(obj.name for obj in bpy.context.selected_objects) == selection

    def invoke():
        with bpy.context.temp_override(window=window, area=area, region=region):
            assert bpy.ops.clothnext.pick_collider("INVOKE_DEFAULT") == {"RUNNING_MODAL"}
        op = next(iter(linked._sessions.values()))
        kinds = {obj.name: kind for obj, kind, _ in op.batches}
        assert source_name not in kinds and "Same" not in kinds and "Normal" not in kinds
        assert kinds["Other"] == "OTHER_GROUP" and kinds["Available"] == "AVAILABLE"
        return op

    def sequence():
        send("ESC", "PRESS", (10, 10))
        yield .2
        send("ESC", "RELEASE", (10, 10))
        yield .2
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.ed.undo_push(message="Linked Collider baseline")
        op = invoke()
        yield .2
        send("MOUSEMOVE", "NOTHING", point(normal))
        yield .2
        send("LEFTMOUSE", "PRESS", point(normal))
        yield .2
        send("LEFTMOUSE", "RELEASE", point(normal))
        yield .2
        assert linked._sessions and op.state.feedback == "Not a Cloth NeXt Collider"
        preserved()
        send("MOUSEMOVE", "NOTHING", (region.width-20, region.height-20))
        yield .2
        send("LEFTMOUSE", "PRESS", (region.width-20, region.height-20))
        yield .2
        send("LEFTMOUSE", "RELEASE", (region.width-20, region.height-20))
        yield .2
        assert linked._sessions
        send("MOUSEMOVE", "NOTHING", point(other))
        yield .2
        assert op.state.hovered is other and op.state.classification == "OTHER_GROUP"
        send("MOUSEMOVE", "NOTHING", point(available))
        yield .2
        assert op.state.hovered is available and op.state.classification == "AVAILABLE"
        if "--screenshot" in sys.argv:
            bpy.ops.screen.screenshot(filepath=sys.argv[sys.argv.index("--screenshot")+1])
        send("LEFTMOUSE", "PRESS", point(available))
        yield .2
        send("LEFTMOUSE", "RELEASE", point(available))
        yield .2
        assert not linked._sessions and linked._handle is None and linked._feedback_handle is None
        assert linked.group_for(available).uuid == linked.group_for(source).uuid
        preserved()
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.ed.undo()
        yield .2
        assert not bpy.data.objects["Available"].cloth_next.collision_settings_group_id
        invoke()
        yield .2
        other_now = bpy.data.objects["Other"]
        old_uuid = linked.group_for(other_now).uuid
        send("MOUSEMOVE", "NOTHING", point(other_now))
        yield .2
        send("LEFTMOUSE", "PRESS", point(other_now))
        yield .2
        send("LEFTMOUSE", "RELEASE", point(other_now))
        yield .2
        assert not linked._sessions and linked._handle is None
        assert linked.group_for(other_now).uuid == old_uuid  # dialog has not moved it
        send("ESC", "PRESS", (300, 300))
        yield .2
        send("ESC", "RELEASE", (300, 300))
        yield .2
        assert linked.group_for(other_now).uuid == old_uuid
        preserved()
        for cancel in ("ESC", "RIGHTMOUSE"):
            invoke()
            yield .2
            send(cancel, "PRESS", (300, 300))
            yield .2
            send(cancel, "RELEASE", (300, 300))
            yield .2
            assert not linked._sessions and linked._handle is None
            preserved()
        invoke()
        yield .2
        cloth_next.unregister()
        send("MOUSEMOVE", "NOTHING", (300, 300))
        yield .2
        assert not linked._sessions and linked._handle is None and linked._feedback_handle is None
        assert not bpy.app.timers.is_registered(linked._prune)
        print("Linked Collider real picking, hover, invalid/empty clicks, selection, undo, cancel and teardown passed", flush=True)
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
