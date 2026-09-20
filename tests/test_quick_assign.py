"""Pure sector geometry plus Blender adapter dispatch/lifecycle regressions."""
import importlib
import math
from types import SimpleNamespace as NS

import pytest

from cloth_next.quick_assign import Gesture, ROLE_ORDER, make_layout, fan_progress


def test_fan_reveal_staggers_eases_and_finishes():
    assert all(fan_progress(0., i) == 0. for i in range(5))
    assert fan_progress(.03, 0) > fan_progress(.03, 1) > fan_progress(.03, 2)
    assert fan_progress(.12, 0) > .5
    assert all(fan_progress(.4, i) == 1. for i in range(5))
    for i in range(5):
        values = [fan_progress(step / 100., i) for step in range(41)]
        assert values == sorted(values)


@pytest.mark.parametrize("scale", [.75, 1., 1.5, 2.])
def test_layout_order_and_bubble_sector_alignment(scale):
    layout = make_layout((400*scale, 90*scale), 800*scale, 600*scale, scale)
    assert layout.rotation == 0
    assert layout.roles == ROLE_ORDER
    assert layout == make_layout(layout.center, layout.width, layout.height, scale)
    for i, role in enumerate(ROLE_ORDER):
        assert layout.hit(layout.point(i)) == role
        # Well away from every visible bubble, but still in its wedge.
        point = layout.point(i, 35*scale)
        assert math.dist(point, layout.point(i)) > layout.bubble_radius
        assert layout.hit(point) == role


@pytest.mark.parametrize("center", [(250, 40), (250, 360), (40, 200), (460, 200)])
def test_edge_rotation_keeps_order_and_hit_regions_aligned(center):
    layout = make_layout(center, 500, 400)
    assert layout.roles == ROLE_ORDER
    for i, role in enumerate(ROLE_ORDER):
        x, y = layout.point(i)
        assert 18 < x < 482 and 18 < y < 382
        assert layout.hit((x, y)) == role


@pytest.mark.parametrize("count", [1, 3, 5])
def test_sector_width_adapts_to_item_count(count):
    layout = make_layout((300, 100), 600, 500, roles=ROLE_ORDER[:count])
    for i, role in enumerate(layout.roles):
        angle = layout.angle(i)
        for offset in (-.49, .49):
            a = angle + offset*math.pi/count
            assert layout.hit((300+70*math.cos(a), 100+70*math.sin(a))) == role


def test_deadzone_boundaries_and_outside_cancellation():
    layout = make_layout((300, 100), 600, 500)
    assert layout.hit(layout.center) is None
    assert layout.hit(layout.point(2, layout.deadzone)) is None
    assert layout.hit(layout.point(2, layout.inner_radius-.01)) is None
    assert layout.hit(layout.point(2, layout.inner_radius)) == "RIGID_BODY"
    assert layout.hit(layout.point(2, layout.outer_radius+.01)) is None
    assert layout.hit((300, 20)) is None  # outside the upward half-circle
    assert layout.hit((-1, 100)) is None


def test_hold_drag_switch_release_once_and_cancel():
    layout = make_layout((300, 100), 600, 500)
    gesture = Gesture(layout, 0)
    gesture.update(layout.center, .1)
    assert gesture.state == "PRESSED"
    gesture.update(layout.center, .19)
    assert gesture.state == "RADIAL_OPEN"
    for i, role in enumerate(ROLE_ORDER):
        gesture.update(layout.point(i, 70), .2)
        assert gesture.state == "TARGET_HOVER" and gesture.target == role
    assert gesture.release(layout.point(0, 70), .3) == "CLOTH"
    assert gesture.release(layout.point(0, 70), .4) is None
    for point in (layout.center, (300, 20), layout.point(0, 200)):
        gesture = Gesture(layout, 0)
        gesture.update(layout.point(0), .01)  # fast drag activates immediately
        assert gesture.state == "TARGET_HOVER"
        assert gesture.release(point, .02) is None
    gesture = Gesture(layout, 0)
    assert gesture.release(layout.center, .05) is None
    gesture = Gesture(layout, 0)
    gesture.update(layout.point(0), .01)
    gesture.cancel()
    assert gesture.release(layout.point(0), .2) is None


def test_disabled_roles_leave_empty_sectors():
    layout = make_layout((300, 100), 600, 500)
    gesture = Gesture(layout, 0)
    assert gesture.release(layout.point(1), .1, ("CLOTH",)) is None


def test_small_region_shrinks_bubbles_and_sectors_together():
    layout = make_layout((75, 55), 150, 110)
    assert layout.scale < 1
    for i, role in enumerate(ROLE_ORDER):
        x, y = layout.point(i)
        r = layout.bubble_radius*1.12
        assert r <= x <= 150-r and r <= y <= 110-r
        assert layout.hit((x, y)) == role


@pytest.mark.parametrize("center", [(250, 40), (250, 360), (40, 200), (460, 200)])
def test_visible_wedge_tracks_selected_sector_after_rotation(center):
    layout = make_layout(center, 500, 400)
    for i, role in enumerate(ROLE_ORDER):
        vertices, alpha, triangles = layout.sector_mesh(i)
        assert len(vertices) == len(alpha) == 63
        assert len(triangles) == 80
        # Interior samples from the middle ring must select this same role.
        assert all(layout.hit(point) == role for point in vertices[22:41])
        assert alpha[0] > alpha[21] > alpha[42] == 0


def test_empty_layout_has_no_target():
    layout = make_layout((100, 100), 300, 300, roles=())
    assert layout.hit((150, 150)) is None


def adapter():
    return importlib.import_module("cloth_next.blender.quick_assign")


def test_quick_add_uses_compact_role_labels_only(blender_env):
    quick = adapter()
    assert quick.QUICK_ROLE_LABELS == {
        "ROD": "CABLE",
        "SOFT_BODY": "SBD",
        "RIGID_BODY": "RBD",
        "COLLIDER": "COLL",
    }
    # The normal role selector keeps its descriptive names.
    labels = {role: label for role, label, _ in blender_env.object_properties.ROLE_ITEMS}
    assert labels["ROD"] == "Cable / Rope"
    assert labels["SOFT_BODY"] == "Soft Body"
    assert labels["COLLIDER"] == "Collider"


def test_role_mapping_and_existing_assignment_dispatch(blender_env, monkeypatch):
    quick = adapter()
    assert set(ROLE_ORDER) <= {item[0] for item in blender_env.object_properties.ROLE_ITEMS}
    calls = []
    obj = NS(type="MESH", cloth_next=NS(enabled=False))
    context = NS(active_object=obj, mode="OBJECT")
    monkeypatch.setattr(blender_env.bpy.ops, "clothnext", NS(
        add_physics=lambda *args: calls.append(("add", args)) or {"FINISHED"},
        set_object_type=lambda *args, **kw: calls.append((kw["role"], args)) or {"FINISHED"}))
    operator = quick.CLOTHNEXT_OT_quick_assign_role()
    operator.role = "RIGID_BODY"
    assert operator.execute(context) == {"FINISHED"}
    assert calls == [("add", ("EXEC_DEFAULT", False)), ("RIGID_BODY", ("EXEC_DEFAULT", False))]
    calls.clear()
    operator.role = "ROD"
    assert operator.execute(context) == {"CANCELLED"} and calls == []
    obj.type = "CURVE"
    assert quick.valid_roles(context) == ("ROD",)
    obj.cloth_next.enabled = True
    assert operator.execute(context) == {"FINISHED"}
    assert calls == [("ROD", ("EXEC_DEFAULT", False))]


def test_mixed_selection_does_not_claim_current_role(blender_env):
    quick = adapter()
    objects = [NS(cloth_next=NS(enabled=True, role=role)) for role in ("CLOTH", "COLLIDER")]
    context = NS(active_object=objects[0], selected_objects=objects)
    assert quick.current_role(context) is None
    objects[1].cloth_next.role = "CLOTH"
    assert quick.current_role(context) == "CLOTH"
    objects[1].cloth_next.enabled = False
    assert quick.current_role(context) is None


def live_session(quick, monkeypatch):
    removed, assigned = [], []
    operator = quick.CLOTHNEXT_OT_quick_assign()
    operator.key = (1, 2)
    operator.gesture = Gesture(make_layout((300, 100), 600, 500), 0)
    operator.area = NS(tag_redraw=lambda: None)
    operator.region = NS(x=0, y=0)
    operator.allowed = ROLE_ORDER
    operator.timer = object()
    operator.wm = NS(event_timer_remove=removed.append)
    quick._sessions[operator.key] = operator
    monkeypatch.setattr(operator, "valid_context", lambda context: True)
    monkeypatch.setattr(quick.bpy.ops, "clothnext", NS(
        quick_assign_role=lambda *args, **kw: assigned.append(kw["role"])))
    return operator, removed, assigned


@pytest.mark.parametrize("event_type", ["ESC", "RIGHTMOUSE", "WINDOW_DEACTIVATE"])
def test_cancel_has_no_assignment_and_removes_timer(blender_env, monkeypatch, event_type):
    quick = adapter()
    operator, removed, assigned = live_session(quick, monkeypatch)
    operator.gesture.update(operator.gesture.layout.point(0), .3)
    assert operator.modal(NS(), NS(type=event_type, value="PRESS")) == {"CANCELLED"}
    operator.cancel(NS())
    assert len(removed) == 1 and assigned == [] and quick._sessions == {}


def test_release_retests_position_and_dispatches_exactly_once(blender_env, monkeypatch):
    quick = adapter()
    operator, removed, assigned = live_session(quick, monkeypatch)
    operator.gesture.update(operator.gesture.layout.point(0), .3)
    x, y = operator.gesture.layout.point(4, 60)
    event = NS(type="LEFTMOUSE", value="RELEASE", mouse_x=x, mouse_y=y)
    assert operator.modal(NS(), event) == {"FINISHED"}
    assert operator.modal(NS(), event) == {"CANCELLED"}
    assert assigned == ["COLLIDER"] and len(removed) == 1


def test_invalidation_and_unregister_cancel_without_assignment(blender_env, monkeypatch):
    quick = adapter()
    operator, removed, assigned = live_session(quick, monkeypatch)
    monkeypatch.setattr(operator, "valid_context", lambda context: False)
    assert operator.modal(NS(), NS(type="TIMER")) == {"CANCELLED"}
    assert len(removed) == 1 and not assigned
    operator, removed, assigned = live_session(quick, monkeypatch)
    floating = importlib.import_module("cloth_next.blender.floating_simulation")
    floating.unregister()
    assert len(removed) == 1 and not assigned and not quick._sessions


def test_quick_button_is_centered_above_toolbar(blender_env, monkeypatch):
    quick = adapter()
    floating = importlib.import_module("cloth_next.blender.floating_simulation")
    monkeypatch.setattr(floating, "_animated_bounds", lambda context: (100, 20, 200, 40, .65))
    x, y, radius, scale = quick.button_bounds(NS())
    assert x == 200 and y-radius > 60 and scale == 1


@pytest.mark.parametrize("rotation", [0, math.pi, -math.pi/2, math.pi/2])
def test_captions_stay_upright_when_fan_rotates(rotation):
    from dataclasses import replace
    layout = replace(make_layout((300, 100), 600, 500), rotation=rotation)
    for index in range(len(ROLE_ORDER)):
        assert -math.pi/2 <= layout.label_angle(index) <= math.pi/2
    if rotation == 0:
        for role in ("SOFT_BODY", "COLLIDER"):
            index = ROLE_ORDER.index(role)
            assert layout.label_angle(index) == pytest.approx(layout.angle(index)-math.pi)
