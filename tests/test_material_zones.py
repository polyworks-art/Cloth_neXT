# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from cloth_next.materials.zones import (
    MaterialZone, ZoneError, ScreenBins, assign_faces, topology_signature,
    triangle_tables, validate_tables, visible_center)
from cloth_next.ppf.official_scene_bridge import bind_face_friction
from cloth_next.ppf.schema.data import SceneObject
from cloth_next.ppf.coordinates import solver_world_matrix


def zone(token=1, **props):
    return MaterialZone(str(uuid4()), token, f'Zone {token}', True, tuple(props.items()))


def test_hard_shared_vertex_boundary_reaches_actual_triangle_binding():
    # These triangles share TWO vertices. No vertex weights are constructed.
    faces = ((0, 1, 2), (1, 3, 2))
    tables = triangle_tables('Cloth', (0, 1), (zone(bend=100),), (0, 1), {'bend': 10})
    assert tables == {'bend': (10.0, 100.0)}
    # Official frontend is free to reorder triangles and vertex identifiers.
    actual = ((11, 13, 12), (10, 11, 12))
    resolved = bind_face_friction(actual, np.array([10, 10]), {'cloth': (10, 11, 12, 13)},
                                 [{'uuid': 'cloth', 'faces': faces, 'values': tables['bend']}])
    assert resolved.tolist() == [100.0, 10.0]


def test_ownership_transfer_remove_delete_and_reorder():
    a, b = zone(1, bend=100), zone(2, bend=200)
    owners = assign_faces((0, 0, 0), (0, 1), a.token)
    assert owners == (1, 1, 0)
    owners = assign_faces(owners, (1,), b.token)
    assert owners == (1, 2, 0)
    assert assign_faces(owners, (0, 1), a.token, remove=True) == (0, 2, 0)
    base = {'bend': 10}
    assert triangle_tables('Cloth', owners, (a, b), (0, 1, 2), base) == triangle_tables('Cloth', owners, (b, a), (0, 1, 2), base)
    owners = tuple(0 if token == b.token else token for token in owners)
    assert triangle_tables('Cloth', owners, (a,), (0, 1, 2), base)['bend'] == (100, 10, 10)


def test_quad_ngon_multiple_zones_inheritance_disabled_and_friction():
    a, b = zone(1, bend=100), zone(2, **{'young-mod': 300})
    mapping = (0, 0, 1, 1, 1, 2)
    tables = triangle_tables('Cloth', (1, 2, 0), (a, b), mapping, {'bend': 10, 'young-mod': 20})
    assert tables['bend'] == (100, 100, 10, 10, 10, 10)
    assert tables['young-mod'] == (20, 20, 300, 300, 300, 20)
    assert triangle_tables('Cloth', (1,), (replace(a, enabled=False),), (0,), {'bend': 10}) == {}
    grip = zone(3, friction=.5)
    assert triangle_tables('Cloth', (0, 3), (grip,), (0, 1), {'friction': .1}, (.2, .3))['friction'] == pytest.approx((.2, .5))


@pytest.mark.parametrize('props', ({'pressure': 1}, {'bend': float('nan')}, {'bend': -1}, {'young-mod': 0}, {'friction': 1}))
def test_invalid_property_values_name_zone_and_remedy(props):
    with pytest.raises(ZoneError, match='Zone 1'):
        triangle_tables('Cloth', (1,), (zone(**props),), (0,), {'bend': 10})


def test_invalid_identity_owners_duplicate_and_array_lengths():
    a = zone(bend=100)
    for zones, owners in (((replace(a, identity='invalid'),), (1,)), ((a, a), (1,)), ((a,), (2,))):
        with pytest.raises(ZoneError):
            triangle_tables('Cloth', owners, zones, (0,), {'bend': 10})
    with pytest.raises(ZoneError, match='triangle values'):
        validate_tables('Cloth', {'bend': (10,)}, 2)
    with pytest.raises(ZoneError, match='repeats'):
        triangle_tables('Cloth', (1,), (replace(a, overrides=(('bend', 1), ('bend', 2))),), (0,), {'bend': 10})


def test_no_zones_keeps_old_payload_byte_structure():
    matrix = solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)))
    obj = SceneObject('Cloth', 'cloth', ((0, 0, 0), (1, 0, 0), (0, 1, 0)), ((0, 1, 2),), matrix)
    assert 'face_material_params' not in obj.info_dict()
    assert obj.info_dict() == replace(obj, face_material_params={}).info_dict()
    assert replace(obj, face_material_params={'bend': (100,)}).info_dict()['face_material_params'] == {'bend': [100]}
    with pytest.raises(ZoneError):
        replace(obj, face_material_params={'bend': (1, 2)})


def test_screen_bins_trigger_only_center_not_polygon_overlap():
    points = ((0, 0), (100, 100), None, (10, 0), (10.001, 0))
    assert tuple(ScreenBins(points).circle((0, 0), 10)) == (0, 3)
    # A large polygon crossing the circle is irrelevant: its center is outside.
    assert tuple(ScreenBins(((40, 0),)).circle((0, 0), 10)) == ()


@pytest.mark.parametrize('origin', ((0, 0, 10), (0, 0, 100)))
def test_first_hit_visibility_not_normal_direction(origin):
    def ray(_origin, _direction):
        return ((True, 0), (0, 0, 0))
    assert visible_center((0, 0, 0), origin, (0, 0, -1), ray, (True, 0), 1e-6)
    assert not visible_center((0, 0, -1), origin, (0, 0, -1), ray, (True, 1), 1e-6)
    assert not visible_center((0, 0, -1), origin, (0, 0, -1), ray, (True, 0), 1e-6)


def test_topology_hash_detects_same_count_connectivity_changes():
    assert topology_signature(4, ((0, 1, 2), (1, 3, 2))) != topology_signature(4, ((0, 1, 3), (0, 3, 2)))


def test_material_panel_actions_counts_and_cloth_only(blender_env):
    from tests.test_phase3b_material_ui import RecordingLayout, _settings, _context
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    z = settings.material_zones.add()
    z.identity, z.token, z.face_count = str(uuid4()), 1, 142
    panel = env.physics_ui.CLOTHNEXT_PT_material()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert 'Material Zones' in panel.layout.labels
    assert '142 Faces' in panel.layout.labels
    actions = {a for a, _text in panel.layout.operators}
    assert {'clothnext.add_material_zone', 'clothnext.remove_material_zone', 'clothnext.edit_material_zone_selection', 'clothnext.material_zone_property'} <= actions
    assert not {'weight', 'falloff', 'xray', 'select_through'} & set(panel.layout.props)
    assert panel.layout.props.index('bend_rest_from_geometry') < panel.layout.props.index('expanded')
    for role in ('ROD', 'SOFT_BODY', 'RIGID_BODY'):
        settings.role = role
        panel.layout = RecordingLayout()
        panel.draw(_context(obj))
        assert 'Material Zones' not in panel.layout.labels


def test_selector_cleanup_is_idempotent_and_cancel_keeps_assignments(blender_env, monkeypatch):
    from cloth_next.blender import material_zone_selector as selector
    operator = selector.CLOTHNEXT_OT_edit_material_zone_selection()
    removed = []
    monkeypatch.setattr(blender_env.bpy.types.SpaceView3D, 'draw_handler_remove', lambda h, _slot: removed.append(h))
    operator._handles, operator._closed = [1, 2], False
    operator._area = SimpleNamespace(type='VIEW_3D', header_text_set=lambda _: None, tag_redraw=lambda: None)
    operator._start, operator._owners = (0, 1), (1, 1)
    selector._sessions.add(operator)
    operator.cancel(None)
    operator.cancel(None)
    assert removed == [1, 2] and not selector._sessions
    assert operator._trees == () and operator._obj is None


def test_zone_fingerprint_cheap_and_reorder_invariant(blender_env):
    from cloth_next.blender.material_zones import settings_record
    from tests.test_phase3b_material_ui import _settings
    blender_env.registration.register()
    obj, settings = _settings(blender_env)
    for token in (1, 2):
        z = settings.material_zones.add()
        z.identity, z.token = str(uuid4()), token
    before = settings_record(obj)
    settings.material_zones.reverse()
    assert settings_record(obj) == before
    settings.material_zone_digest = 'new ownership'
    assert settings_record(obj) != before


@pytest.mark.parametrize('finish', ('ESC', 'RET'))
def test_modal_add_shift_remove_radius_commit_cancel_and_cleanup(blender_env, monkeypatch, finish):
    from cloth_next.blender import material_zone_selector as selector
    from cloth_next.blender import material_zones as data
    op = selector.CLOTHNEXT_OT_edit_material_zone_selection()
    op._closed, op._handles = False, [object(), object()]
    op._area = SimpleNamespace(type='VIEW_3D', header_text_set=lambda _: None, tag_redraw=lambda: None)
    op._region = SimpleNamespace(x=0, y=0, width=800, height=600)
    op._owners = op._start = (0, 2)
    op._token, op._radius, op._drag = 1, 40, False
    op._geometry_dirty, op._hover = False, ()
    op._mesh, op._obj, op._topology = object(), object(), 'topology'
    op._valid = lambda context: True
    op._visible_candidates = lambda: (0,)
    selector._sessions.add(op)
    committed = []
    monkeypatch.setattr(data, 'signature', lambda mesh: 'topology')
    monkeypatch.setattr(data, 'read_owners', lambda mesh: (0, 2))
    monkeypatch.setattr(data, 'write_owners', lambda obj, owners: committed.append(owners))
    def event(kind, shift=False, value='PRESS'):
        return SimpleNamespace(type=kind, value=value, shift=shift, mouse_x=400, mouse_y=300)
    assert op.modal(None, event('LEFTMOUSE')) == {'RUNNING_MODAL'}
    assert op._owners == (1, 2)
    op.modal(None, event('WHEELUPMOUSE', shift=True))
    assert op._owners == (1, 2) and op._radius > 40
    op.modal(None, event('MOUSEMOVE', shift=True))
    assert op._owners == (0, 2)
    op.modal(None, event('MOUSEMOVE'))
    assert op._owners == (1, 2)
    assert not committed
    result = op.modal(None, event(finish))
    assert result == ({'FINISHED'} if finish == 'RET' else {'CANCELLED'})
    assert committed == ([(1, 2)] if finish == 'RET' else [])
    assert not op._handles and not selector._sessions and op._obj is None


def test_modal_exception_load_unregister_cleanup(blender_env, monkeypatch):
    from cloth_next.blender import material_zone_selector as selector
    for stop in ('exception', 'load', 'unregister'):
        op = selector.CLOTHNEXT_OT_edit_material_zone_selection()
        op._closed, op._handles = False, [object(), object()]
        op._area = SimpleNamespace(type='VIEW_3D', header_text_set=lambda _: None, tag_redraw=lambda: None)
        selector._sessions.add(op)
        if stop == 'exception':
            def fail(_):
                raise ReferenceError('object deleted')
            op._valid, op.report = fail, lambda *args: None
            assert op.modal(None, None) == {'CANCELLED'}
        elif stop == 'load':
            selector._load_pre()
        else:
            selector.unregister()
        assert not op._handles and not selector._sessions and op._centers == ()


def test_actual_managed_scalar_expansion_preserves_exact_boundary():
    import textwrap
    from cloth_next.ppf import solver_overlay as overlay
    body = textwrap.dedent(overlay._SCENE_EXTEND_REPLACEMENT)
    source = "def expand(value, count, per_element):\n    concat_param = {'bend': []}\n    key = 'bend'\n"
    source += textwrap.indent(body, '    ') + "\n    return concat_param['bend']\n"
    namespace = {'np': np}
    exec(source, namespace)
    assert namespace['expand'](10, 2, {'bend': (10, 100)}) == [10, 100]
    assert namespace['expand'](10, 2, None) == [10, 10]
    with pytest.raises(ValueError, match='count'):
        namespace['expand'](10, 2, {'bend': (100,)})


def test_native_material_writer_reordered_triangles_and_atomic_validation(tmp_path):
    from cloth_next.ppf.official_scene_worker import apply_material_tables
    binary = tmp_path / 'bin'
    (binary / 'param').mkdir(parents=True)
    np.asarray(((1, 3, 2), (0, 1, 2)), dtype=np.uint64).tofile(binary / 'tri.bin')
    np.asarray((10, 10), dtype=np.float32).tofile(binary / 'param' / 'tri-bend.bin')
    row = {'uuid': 'cloth', 'faces': ((0, 1, 2), (1, 3, 2)), 'params': {'bend': (10, 100)}}
    apply_material_tables(binary, {'cloth': (0, 1, 2, 3)}, (row,))
    assert np.fromfile(binary / 'param' / 'tri-bend.bin', dtype=np.float32).tolist() == [100, 10]
    with pytest.raises(ValueError, match='count mismatch'):
        apply_material_tables(binary, {'cloth': (0, 1, 2, 3)}, ({**row, 'params': {'bend': (9,)}},))
    assert np.fromfile(binary / 'param' / 'tri-bend.bin', dtype=np.float32).tolist() == [100, 10]


def test_bake_lock_refuses_all_zone_mutation_operators(blender_env, monkeypatch):
    from cloth_next.blender import material_zones as data
    from tests.test_phase3b_material_ui import _settings, _context
    blender_env.registration.register()
    obj, _ = _settings(blender_env)
    obj.mode = 'OBJECT'
    monkeypatch.setattr(data.shared_controller, 'snapshot', lambda: SimpleNamespace(active=True))
    for cls in data.CLASSES:
        assert not cls.poll(_context(obj))


def test_managed_profiles_and_external_capability_fail_loudly(blender_env):
    from cloth_next.blender.material_zones import require_capability
    from cloth_next.ppf.resolver import SolverMode
    from tests.test_phase3b_material_ui import _settings
    blender_env.registration.register()
    obj, settings = _settings(blender_env)
    settings.material_zones.add().name = 'Collar'
    for protocol in ('0.13', '0.18', '0.22', '0.23'):
        resolved = SimpleNamespace(mode=SolverMode.MANAGED_INSTALLATION, protocol_version=protocol, schema_version='2')
        require_capability(obj, resolved)
    for mode, protocol in ((SolverMode.DEVELOPMENT, '0.23'), (SolverMode.MANAGED_INSTALLATION, '0.99')):
        with pytest.raises(ZoneError, match='Collar.*managed'):
            require_capability(obj, SimpleNamespace(mode=mode, protocol_version=protocol, schema_version='2'))
