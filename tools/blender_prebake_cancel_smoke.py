# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender boundary evaluation cancelled by authenticated Companion IPC."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
import cloth_next
from cloth_next.bake.status import BakeState
from cloth_next.bake.transport import LocalSocketClient, LocalSocketServer
from cloth_next.blender import solver_test as bake, companion_manager
from cloth_next.blender.playback_cache import ensure_simulation_modifier

cloth_next.register()
obj = bpy.context.object
obj.cloth_next.enabled = True
prefix = obj.modifiers.new('Upstream deformation', 'DISPLACE')
prefix.strength = .1
boundary = ensure_simulation_modifier(obj)
obj.modifiers.move(list(obj.modifiers).index(prefix), 0)
suffix = obj.modifiers.new('Downstream detail', 'SUBSURF')
flags = tuple((m, m.show_viewport, m.show_render) for m in obj.modifiers)
original_frame = bpy.context.scene.frame_current
server = LocalSocketServer()
client = LocalSocketClient(server.port, server.token)
old_server = companion_manager._server
old_ensure = companion_manager.ensure_running
old_validate = bake.validate_scene
old_extract = bake._extract_mesh
companion_manager._server = server
companion_manager.ensure_running = lambda: (True, 'test transport')


def extract_then_cancel(*args, **kwargs):
    old_extract(*args, **kwargs)
    client.request_cancel()
    deadline = time.monotonic() + 2
    while server.requests.empty() and time.monotonic() < deadline:
        time.sleep(.01)
    bake._check_preparation_cancel()
    raise AssertionError('Geometry preparation continued after authenticated Cancel')


def validate_with_boundary(context):
    bake._extract_deformable_mesh(context, obj, needs_edges=True)
    raise AssertionError('Validation continued after Cancel')


bake._extract_mesh = extract_then_cancel
bake.validate_scene = validate_with_boundary
try:
    try:
        bake.begin_production_bake(bpy.context)
    except bake.SessionCancelled:
        pass
    else:
        raise AssertionError('Bake was not cancelled')
    assert bake.shared_controller.snapshot().state is BakeState.CANCELLED
    assert bake._worker is None and bake._pending_plan is None
    assert not any(o.name.startswith('__ClothNeXtBoundary_') for o in bpy.data.objects)
    assert all(m.show_viewport == viewport and m.show_render == render for m, viewport, render in flags)
    assert bpy.context.scene.frame_current == original_frame
    assert bake.modal_lock.reserve('next-attempt')
    bake.modal_lock.release('next-attempt')
    print('PREBAKE_CANCEL_SMOKE_OK: authenticated IPC, no timer polling, evaluated mesh cleanup, modifier/frame preservation, no worker, reservation released')
finally:
    bake._extract_mesh = old_extract
    bake.validate_scene = old_validate
    companion_manager.ensure_running = old_ensure
    companion_manager._server = old_server
    client.close()
    server.close()
    cloth_next.unregister()
