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
    floating._resume_slide = 0.0
    floating._resume_animation_from = 0.0
    floating._resume_animation_target = False
    floating._resume_animation_start_time = None
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


def test_pull_right_resumes_through_existing_operator(pull, monkeypatch):
    f, c, _targets = pull
    c.scene.cloth_next_recovery = NS(resumable=True)
    f._resume_slide = 1.0
    f._resume_animation_target = True
    monkeypatch.setattr(f, "_animated_bounds", lambda _c: (100, 20, 340, 54, 1))
    monkeypatch.setattr(f, "_quality_width", lambda _c: 66)
    calls = []
    monkeypatch.setattr(
        f.bpy.ops.clothnext, "recovery_resume_latest",
        lambda mode: calls.append(mode) or {"FINISHED"}, raising=False)
    op = f.CLOTHNEXT_OT_pull_resume()
    assert op.invoke(c, event("LEFTMOUSE", x=400)) == {"RUNNING_MODAL"}
    assert op.modal(c, event("MOUSEMOVE", x=560)) == {"RUNNING_MODAL"}
    assert op.gesture.state == "ARMED"
    assert op.modal(c, event("LEFTMOUSE", "RELEASE", 560)) == {"FINISHED"}
    assert calls == ["EXEC_DEFAULT"]


def test_pull_resume_is_hidden_without_resumable_checkpoint(pull):
    f, c, _targets = pull
    c.scene.cloth_next_recovery = NS(resumable=False)
    assert not f.CLOTHNEXT_OT_pull_resume.poll(c)


def test_resume_segment_animates_only_for_resumable_checkpoint(pull):
    f, c, _targets = pull
    c.scene.cloth_next_recovery = NS(resumable=False)
    assert f._resume_fraction(c, now=10.0) == 0.0
    c.scene.cloth_next_recovery = NS(
        resumable=True, latest_checkpoint_frame=19)
    assert f._resume_fraction(c, now=10.0) == 0.0
    halfway = f._resume_fraction(
        c, now=10.0 + f._RESUME_ANIMATION_DURATION / 2)
    assert 0.5 < halfway < 1.0
    assert f._resume_fraction(
        c, now=10.0 + f._RESUME_ANIMATION_DURATION) == 1.0


def test_toolbar_background_expands_with_resume_segment(pull, monkeypatch):
    f, c, _targets = pull
    c.region.width = 1000
    c.region.height = 500
    c.preferences = NS(system=NS(ui_scale=1.0))
    c.scene.cloth_next_recovery = NS(
        resumable=True, latest_checkpoint_frame=19)
    monkeypatch.setattr(f, "_quality_width", lambda _c: 66)
    f._resume_slide = 1.0
    f._resume_animation_target = True
    _x, _y, width, _height, scale = f._bounds(c)
    assert width == pytest.approx((230 + 66 + f._RESUME_EXTENSION) * scale)
