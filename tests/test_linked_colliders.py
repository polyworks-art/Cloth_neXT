"""Shared-data, classification, context and transient-picker regressions."""
from types import SimpleNamespace as NS
import pytest
from tests.fake_bpy import _instantiate_group


@pytest.fixture
def env(blender_env, monkeypatch):
    from cloth_next.blender import linked_colliders as linked
    blender_env.registration.register()
    scene = blender_env.bpy.types.Scene()
    objects = [blender_env.bpy.types.Object(name=f"Collider {i}") for i in range(5)]
    scene.objects = objects
    for obj in objects:
        obj.users_scene = (scene,)
        obj.cloth_next.enabled = True
        obj.cloth_next.role = "COLLIDER"
    layer = NS(objects=objects)
    yield NS(linked=linked, scene=scene, objects=objects, layer=layer, blender=blender_env)
    monkeypatch.undo()
    blender_env.registration.unregister()


def test_real_sharing_all_four_fields_and_export(env):
    e = env
    a, b, c = e.objects[:3]
    a.cloth_next.collision.surface_grip = .3
    b.cloth_next.collision.surface_grip = .8
    assert e.linked.group_for(a, e.scene) is None
    assert e.linked.effective_collider_settings(a).surface_grip == .3
    assert e.linked.link(e.scene, a, b)
    group = e.linked.group_for(a, e.scene)
    assert group.uuid and e.linked.group_for(b, e.scene) is group
    assert group.surface_grip == .3
    assert b.cloth_next.collision.surface_grip == .8  # no copying to synchronize
    assert e.linked.link(e.scene, a, c)
    group.collider_motion, group.surface_grip, group.collision_gap, group.surface_offset = "ANIMATED", .7, .004, .002
    for obj in (a, b, c):
        values = e.linked.effective_collider_settings(obj)
        assert values is group
        assert (values.collider_motion, values.surface_grip, values.collision_gap, values.surface_offset) == ("ANIMATED", .7, .004, .002)
        snapshot = e.blender.object_properties.static_settings_from(obj.cloth_next)
        assert (snapshot.surface_grip, snapshot.collision_gap, snapshot.surface_offset) == (.7, .004, .002)
        assert e.blender.object_properties.collider_motion_from(obj.cloth_next) == "ANIMATED"
    assert len(e.linked.members(e.scene, group)) == 3


def test_unlink_preserves_values_and_dissolves(env):
    e = env
    a, b = e.objects[:2]
    e.linked.link(e.scene, a, b)
    group = e.linked.group_for(a)
    group.surface_grip = .65
    group.collider_motion = "ANIMATED"
    assert e.linked.unlink(e.scene, b)
    assert not e.scene.cloth_next_collision_groups
    for obj in (a, b):
        assert not obj.cloth_next.collision_settings_group_id
        assert obj.cloth_next.collision.surface_grip == .65
        assert obj.cloth_next.collider_motion == "ANIMATED"


def test_move_requires_confirmation_and_preserves_old_survivor(env):
    e = env
    a, b, c, d = e.objects[:4]
    e.linked.link(e.scene, a, b)
    e.linked.link(e.scene, c, d)
    old = e.linked.group_for(c)
    old.surface_grip = .9
    assert not e.linked.link(e.scene, a, c)
    assert e.linked.group_for(c) is old
    assert e.linked.link(e.scene, a, c, allow_move=True)
    assert e.linked.group_for(a) is e.linked.group_for(c)
    assert e.linked.group_for(d) is None and d.cloth_next.collision.surface_grip == .9
    assert len(e.scene.cloth_next_collision_groups) == 1


def test_classification_and_hidden_invalid_exclusions(env):
    e = env
    a, b, c, d, normal = e.objects
    e.linked.link(e.scene, a, b)
    e.linked.link(e.scene, c, d)
    assert e.linked.classify(e.scene, e.layer, a, b) == "SAME_GROUP"
    assert e.linked.classify(e.scene, e.layer, a, c) == "OTHER_GROUP"
    assert e.linked.classify(e.scene, e.layer, a, normal) == "AVAILABLE"
    normal.cloth_next.role = "CLOTH"
    assert e.linked.classify(e.scene, e.layer, a, normal) == "INVALID"
    assert e.linked.classify(e.scene, e.layer, a, None) == "INVALID"
    assert e.linked.classify(e.scene, e.layer, a, a) == "SAME_GROUP"
    c.visible_get = lambda **kwargs: False
    assert e.linked.classify(e.scene, e.layer, a, c) == "INVALID"


def test_stale_and_deleted_members_repair(env):
    e = env
    a, b = e.objects[:2]
    e.linked.link(e.scene, a, b)
    e.linked.group_for(a).surface_offset = .006
    e.scene.objects.remove(b)
    e.linked.repair_scene(e.scene)
    assert not e.scene.cloth_next_collision_groups
    assert a.cloth_next.collision.surface_offset == .006
    a.cloth_next.collision_settings_group_id = "missing"
    e.linked.repair_scene(e.scene)
    assert not a.cloth_next.collision_settings_group_id


def test_bake_blocks_whole_mutation(env, monkeypatch):
    e = env
    a, b = e.objects[:2]
    e.linked.link(e.scene, a, b)
    monkeypatch.setattr(e.linked.shared_controller, "snapshot", lambda: NS(active=True))
    assert not e.linked.unlink(e.scene, b)
    assert not e.linked.link(e.scene, a, e.objects[2])
    assert e.linked.group_for(b) is e.linked.group_for(a)


def test_shared_edit_uses_dirty_path(env, monkeypatch):
    e = env
    e.linked.link(e.scene, *e.objects[:2])
    dirty = []
    monkeypatch.setattr(e.blender.object_properties.validation_state, "mark_all_settings_dirty", lambda: dirty.append(True))
    e.linked.group_for(e.objects[0]).surface_grip = .81
    assert dirty


def test_pick_state_invalid_empty_same_group_and_hover(env):
    state = env.linked.PickState()
    a, b = env.objects[:2]
    state.update(a, "AVAILABLE")
    assert state.hovered is a and state.click(0) == "AVAILABLE"
    state.update(b, "OTHER_GROUP")
    assert state.hovered is b and state.click(0) == "OTHER_GROUP"
    for kind in ("INVALID", "SAME_GROUP"):
        state.update(None, kind)
        assert state.click(1) is None
        assert state.feedback_until == 2.5


@pytest.mark.parametrize("reason", ["success", "ESC", "RIGHTMOUSE", "unregister"])
def test_temporary_draw_state_teardown_is_idempotent(env, monkeypatch, reason):
    e = env
    linked = e.linked
    removed, restored = [], []
    monkeypatch.setattr(e.blender.bpy.types.SpaceView3D, "draw_handler_remove", lambda handle, region: removed.append(handle))
    window = NS(screen=NS(areas=[]), cursor_modal_restore=lambda: restored.append(1))
    op = linked.CLOTHNEXT_OT_pick_collider()
    op.window, op.key, op.state, op.source = window, 10, linked.PickState(e.objects[1], "AVAILABLE"), e.objects[0]
    op.batches = [(e.objects[1], "AVAILABLE", object())]
    linked._sessions[10] = op
    linked._handle, linked._feedback_handle = "outline", "feedback"
    e.blender.bpy.app.timers.register(linked._prune)
    if reason == "unregister":
        linked.unregister()
    elif reason in {"ESC", "RIGHTMOUSE"}:
        monkeypatch.setattr(op, "alive", lambda: True)
        assert op.modal(None, NS(type=reason, value="PRESS")) == {"CANCELLED"}
    else:
        op.finish()
    op.finish()
    assert removed == ["outline", "feedback"]
    assert not linked._sessions and op.source is None and op.batches == []
    assert linked._handle is None and linked._feedback_handle is None
    assert not e.blender.bpy.app.timers.is_registered(linked._prune)


def test_scene_scope_and_linked_library_excluded(env):
    e = env
    a, b = e.objects[:2]
    b.users_scene = (e.scene, object())
    assert not e.linked.link(e.scene, a, b)
    b.users_scene = (e.scene,)
    b.library = object()
    assert e.linked.classify(e.scene, e.layer, a, b) == "INVALID"


def test_linked_ui_markers_and_direct_shared_property_owners(env, monkeypatch):
    from tests.test_phase3b_material_ui import RecordingLayout
    e = env
    a, b = e.objects[:2]
    ui = e.blender.physics_ui
    owners = []
    original = RecordingLayout.prop
    def record_prop(self, owner, name, **kwargs):
        owners.append((owner, name))
        return original(self, owner, name, **kwargs)
    monkeypatch.setattr(RecordingLayout, "prop", record_prop)
    context = NS(object=a, scene=e.scene)
    unique = RecordingLayout()
    ui._draw_collider_collision(unique, a.cloth_next, context)
    assert all(owner is not e.linked.group_for(a) for owner, name in owners)
    panel = ui.CLOTHNEXT_PT_collider_collision()
    panel.layout = unique
    panel.draw_header(context)
    assert not any(text.startswith("Shared") for _, text in unique.operators)
    e.linked.link(e.scene, a, b)
    owners.clear()
    shared = RecordingLayout()
    ui._draw_collider_collision(shared, a.cloth_next, context)
    group = e.linked.group_for(a)
    assert {name for owner, name in owners if owner is group} == set(e.linked.FIELDS)
    assert a.name in shared.labels and b.name in shared.labels
    panel.layout = shared
    panel.draw_header(context)
    assert ("clothnext.shared_collision_info", "Shared [2]") in shared.operators
    assert shared.labels.count("") == 5  # four property chains and the panel icon


def test_invalid_click_in_real_modal_path_keeps_session(env, monkeypatch):
    e = env
    op = e.linked.CLOTHNEXT_OT_pick_collider()
    op.key, op.window, op.scene, op.view_layer, op.source = 42, NS(screen=NS(areas=[])), e.scene, e.layer, e.objects[0]
    op.state, op.batches = e.linked.PickState(), []
    e.linked._sessions[42] = op
    monkeypatch.setattr(op, "alive", lambda: True)
    event = NS(type="LEFTMOUSE", value="PRESS", mouse_x=20, mouse_y=20)
    assert op.modal(NS(), event) == {"RUNNING_MODAL"}
    assert op.state.feedback == "Not a Cloth NeXt Collider"
    assert e.linked._sessions[42] is op
    op.finish()


def test_deleted_viewport_prunes_overlay(env, monkeypatch):
    e = env
    removed = []
    monkeypatch.setattr(e.blender.bpy.types.SpaceView3D, "draw_handler_remove", lambda handle, region: removed.append(handle))
    op = e.linked.CLOTHNEXT_OT_pick_collider()
    op.key, op.window, op.source, op.state, op.batches = 1, NS(screen=NS(areas=[])), e.objects[0], e.linked.PickState(), []
    e.linked._sessions[1] = op
    e.linked._handle = "viewport"
    monkeypatch.setattr(op, "alive", lambda: False)
    assert e.linked._prune() is None
    assert not e.linked._sessions and removed == ["viewport"]


def test_role_change_and_zero_member_groups_cleanup(env):
    e = env
    a, b = e.objects[:2]
    e.linked.link(e.scene, a, b)
    e.linked.group_for(a).surface_grip = .6
    a.cloth_next.role = "CLOTH"
    e.linked.repair_scene(e.scene)
    assert not e.scene.cloth_next_collision_groups
    assert a.cloth_next.collision.surface_grip == .6
    assert b.cloth_next.collision.surface_grip == .6
    group = e.scene.cloth_next_collision_groups.add()
    group.uuid = "orphan"
    e.linked.repair_scene(e.scene)
    assert not e.scene.cloth_next_collision_groups
