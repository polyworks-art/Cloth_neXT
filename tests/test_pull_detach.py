"""Deterministic pull and state-only batch removal regressions."""
from types import SimpleNamespace as NS
import pytest
from cloth_next.pull_detach import PullGesture, logo_hit


@pytest.mark.parametrize("progress,armed", [(0, False), (.1, False), (.5, False), (.95, True), (1, True)])
def test_release_threshold_and_once(progress, armed):
    gesture = PullGesture(200, 160)
    assert gesture.release(200-160*progress) is armed
    assert gesture.state == "IDLE"
    assert not gesture.release(0)


def test_hysteresis():
    gesture = PullGesture(200, 160)
    gesture.update(48)
    assert gesture.state == "ARMED"
    gesture.update(64)
    assert gesture.state == "ARMED"
    gesture.update(72)
    assert gesture.state == "PULLING"
    assert not gesture.release(72)


def test_hit_testing_scaled():
    assert not logo_hit(None, 0, 0)
    assert logo_hit((100, 20, 300, 108, 2), 150, 50)
    assert not logo_hit((100, 20, 300, 108, 2), 209, 50)


@pytest.fixture
def pull(blender_env, monkeypatch):
    import cloth_next.blender.floating_simulation as floating
    from cloth_next.blender.physics_operators import shared_controller
    monkeypatch.setattr(shared_controller, "snapshot", lambda: NS(active=False))
    monkeypatch.setattr(floating, "visible", lambda c: True)
    monkeypatch.setattr(floating, "_animated_bounds", lambda c: (100, 20, 300, 54, 1))
    floating._registered = True
    floating._animation_start_time = None
    region = NS(type="WINDOW", x=0, y=0, as_pointer=lambda: 2)
    area = NS(type="VIEW_3D", regions=[region], tag_redraw=lambda: None)
    screen = NS(areas=[area])
    window = NS(screen=screen, workspace=object(), as_pointer=lambda: 1)
    targets = [NS(cloth_next=NS(enabled=True, role="CLOTH"), modifiers=[NS(type="MESH_CACHE", filepath="baked.pc2")]) for _ in range(2)]
    context = NS(window=window, area=area, region=region, scene=NS(objects=targets), view_layer=object(), selected_objects=targets, window_manager=NS(windows=[window], modal_handler_add=lambda op: None))
    yield floating, context, targets
    floating._cancel_pulls()


def event(kind, value="PRESS", x=125):
    return NS(type=kind, value=value, mouse_region_x=x, mouse_region_y=40, mouse_x=x, mouse_y=40)


@pytest.mark.parametrize("kind", ["ESC", "RIGHTMOUSE"])
def test_cancel_and_idle(pull, kind):
    f, c, targets = pull
    assert not f._pull_sessions
    op = f.CLOTHNEXT_OT_pull_detach()
    assert op.invoke(c, event("LEFTMOUSE", x=300)) == {"CANCELLED"}
    assert op.invoke(c, event("LEFTMOUSE")) == {"RUNNING_MODAL"}
    assert op.modal(c, event(kind)) == {"CANCELLED"}
    assert not f._pull_sessions and all(o.cloth_next.enabled for o in targets)


def test_captured_mixed_selection_and_playback(pull, tmp_path):
    f, c, targets = pull
    cache = tmp_path / "baked.pc2"
    cache.write_bytes(b"baked data")
    before = [tuple(o.modifiers) for o in targets]
    c.selected_objects = targets + [NS(cloth_next=NS(enabled=False))]
    op = f.CLOTHNEXT_OT_pull_detach()
    assert op.invoke(c, event("LEFTMOUSE")) == {"RUNNING_MODAL"}
    c.selected_objects = []
    assert op.modal(c, event("MOUSEMOVE", x=-35)) == {"RUNNING_MODAL"}
    assert op.gesture.state == "ARMED"
    assert op.modal(c, event("LEFTMOUSE", "RELEASE", -35)) == {"FINISHED"}
    assert op.modal(c, event("LEFTMOUSE", "RELEASE", -35)) == {"CANCELLED"}
    assert all(not o.cloth_next.enabled for o in targets)
    assert before == [tuple(o.modifiers) for o in targets]
    assert cache.read_bytes() == b"baked data"
    assert {"REGISTER", "UNDO"} <= op.bl_options


@pytest.mark.parametrize("unsafe", ["bake", "deleted", "disabled"])
def test_preflight_cancels_whole_batch(pull, monkeypatch, unsafe):
    f, c, targets = pull
    op = f.CLOTHNEXT_OT_pull_detach()
    op.invoke(c, event("LEFTMOUSE"))
    if unsafe == "bake":
        monkeypatch.setattr(f.shared_controller, "snapshot", lambda: NS(active=True))
    elif unsafe == "deleted":
        c.scene.objects = targets[:1]
    else:
        targets[1].cloth_next.enabled = False
    assert op.modal(c, event("LEFTMOUSE", "RELEASE", -35)) == {"CANCELLED"}
    assert targets[0].cloth_next.enabled
    assert not f._pull_sessions


def test_no_targets(pull):
    f, c, targets = pull
    c.selected_objects = []
    assert f.CLOTHNEXT_OT_pull_detach().invoke(c, event("LEFTMOUSE")) == {"CANCELLED"}
