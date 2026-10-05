# SPDX-License-Identifier: GPL-3.0-or-later
"""Circle selection of visible evaluated face centers, with transactional cancel.

No timer, thread, edit-mode toggle, vertex weights, or X-Ray state. GPU depth
only controls display; CPU BVH first-hit identity controls all assignment.
"""
from __future__ import annotations

import math
import builtins
import bpy

from ..bake.controller import shared_controller
from ..materials.zones import ScreenBins, ZoneError, assign_faces, visible_center
from . import material_zones as data, validation_state

_SESSION_KEY = '_clothnext_material_zone_sessions'
if not hasattr(builtins, _SESSION_KEY):
    setattr(builtins, _SESSION_KEY, set())
_sessions = getattr(builtins, _SESSION_KEY)


class CLOTHNEXT_OT_edit_material_zone_selection(data.ZoneOperator, bpy.types.Operator):
    bl_idname = 'clothnext.edit_material_zone_selection'
    bl_label = 'Edit Material Zone Selection'
    bl_options = {'UNDO', 'BLOCKING'}
    identity: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        if not self.poll(context) or _sessions:
            self.report({'ERROR'}, 'Finish the current selection or active Bake before editing Material Zones.')
            return {'CANCELLED'}
        self._handles = []
        self._modifier_states = []
        self._closed = False
        self._obj, self._mesh = context.object, context.object.data
        self._area = (context.area if context.area and context.area.type == 'VIEW_3D'
                      else next((a for a in context.screen.areas if a.type == 'VIEW_3D'), None))
        if self._area is None:
            self.report({'ERROR'}, 'Open a 3D Viewport to edit Material Zone selections.')
            return {'CANCELLED'}
        self._region = next(r for r in self._area.regions if r.type == 'WINDOW')
        self._space = self._area.spaces.active
        self._rv3d = self._space.region_3d
        self._context = context
        self._start, self._owners = None, None
        self._radius, self._cursor, self._drag = 40.0, (0, 0), False
        self._view_key, self._geometry_dirty, self._hover = None, True, ()
        self._triangles, self._centers, self._trees = (), (), ()
        self._gpu_batches = None
        self._gpu_faces = None
        self._topology = data.signature(self._mesh)
        try:
            data.initialize(self._obj)
            zone = data.find_zone(self._obj, self.identity)
            self._token = zone.token
            self._start = self._owners = data.validate_ownership(self._obj)
            self._mesh_identity = self._obj.cloth_next.material_zone_mesh_id
            self._assignment_digest = self._obj.cloth_next.material_zone_digest
            # Display and pick the same pre-simulation surface that Bake exports.
            from .playback_cache import simulation_modifiers
            boundaries = simulation_modifiers(self._obj)
            if len(boundaries) > 1:
                raise ZoneError('Keep one Cloth NeXt simulation modifier before editing Material Zones.')
            if boundaries:
                modifiers = tuple(self._obj.modifiers)
                boundary = next(i for i, modifier in enumerate(modifiers) if modifier == boundaries[0])
                for modifier in modifiers[boundary:]:
                    self._modifier_states.append((modifier, modifier.show_viewport))
                    modifier.show_viewport = False
                context.view_layer.update()
            self._rebuild(context)
            self._handles.append(bpy.types.SpaceView3D.draw_handler_add(self._draw_world, (), 'WINDOW', 'POST_VIEW'))
            self._handles.append(bpy.types.SpaceView3D.draw_handler_add(self._draw_circle, (), 'WINDOW', 'POST_PIXEL'))
            _sessions.add(self)
            validation_state.add_depsgraph_observer(self._changed)
            context.window_manager.modal_handler_add(self)
            self._area.header_text_set('Material Zone · LMB add · Shift LMB remove · Wheel radius · Enter accept · Esc cancel')
            self._area.tag_redraw()
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self._cleanup(restore=True)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def _changed(self, _scene, depsgraph):
        # Attribute writes alone do not invalidate evaluated geometry caches.
        for update in depsgraph.updates:
            if (getattr(update, 'is_updated_geometry', False)
                    or getattr(update, 'is_updated_transform', False)
                    or isinstance(getattr(update, 'id', None), bpy.types.Object)):
                self._geometry_dirty = True
                break

    def _valid(self, context):
        return (not self._closed and self._obj.name in bpy.data.objects
                and self._obj.data == self._mesh and self._obj.mode == 'OBJECT'
                and any(area == self._area for area in context.screen.areas)
                and self._area.type == 'VIEW_3D'
                and self._space == self._area.spaces.active
                and self._obj.visible_get(view_layer=context.view_layer, viewport=self._space)
                and not shared_controller.snapshot().active
                and self._obj.cloth_next.material_zone_mesh_id == self._mesh_identity
                and self._obj.cloth_next.material_zone_digest == self._assignment_digest
                and any(z.identity == self.identity for z in self._obj.cloth_next.material_zones))

    def _rebuild(self, context):
        from mathutils.bvhtree import BVHTree
        depsgraph = context.evaluated_depsgraph_get()
        scene_vertices, scene_triangles, scene_owners, occluders = [], [], [], []
        target_found = False
        for instance in depsgraph.object_instances:
            obj = instance.object
            original = obj.original
            if not original.visible_get(view_layer=context.view_layer, viewport=self._space):
                continue
            if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}:
                continue
            mesh = obj.to_mesh()
            if mesh is None:
                continue
            try:
                world = instance.matrix_world
                vertices = [world @ v.co for v in mesh.vertices]
                polygons = [tuple(p.vertices) for p in mesh.polygons]
                mesh.calc_loop_triangles()
                occluders.extend(vertices[i] for t in mesh.loop_triangles for i in t.vertices)
                is_target = original == self._obj and not instance.is_instance
                if is_target:
                    if data.signature(mesh) != self._topology:
                        raise ZoneError('Topology before Cloth NeXt differs from authored faces. Apply upstream topology modifiers, clear selections and reselect faces.')
                    from mathutils.geometry import closest_point_on_tri
                    # A non-planar quad's median need not lie on its visible
                    # tessellation. Project it onto its own polygon's surface;
                    # concave polygons likewise get a stable interior trigger.
                    centers = [p.center.copy() for p in mesh.polygons]
                    best = [float('inf')] * len(centers)
                    for tri in mesh.loop_triangles:
                        index = tri.polygon_index
                        target = mesh.polygons[index].center
                        nearest = closest_point_on_tri(target, *(mesh.vertices[i].co for i in tri.vertices))
                        distance = (nearest-target).length_squared
                        if distance < best[index]:
                            best[index], centers[index] = distance, nearest
                    self._centers = tuple(world @ center for center in centers)
                    mesh.calc_loop_triangles()
                    self._triangles = tuple((t.polygon_index, tuple(vertices[i] for i in t.vertices)) for t in mesh.loop_triangles)
                    target_found = True
                if polygons:
                    offset = len(scene_vertices)
                    scene_vertices.extend(vertices)
                    for tri in mesh.loop_triangles:
                        scene_triangles.append(tuple(offset+i for i in tri.vertices))
                        scene_owners.append((is_target, tri.polygon_index))
            finally:
                obj.to_mesh_clear()
        if not target_found:
            raise ZoneError('The Cloth object is no longer visible in this viewport.')
        # One scene BVH avoids one raycast per object per brush candidate. Use
        # Blender's actual evaluated tessellation, including non-planar quads.
        self._trees = ((BVHTree.FromPolygons(scene_vertices, scene_triangles, all_triangles=True),
                        tuple(scene_owners)),)
        self._occluders = tuple(occluders)
        self._gpu_batches = None
        self._gpu_faces = None
        self._polygon_triangles = {}
        for polygon, tri in self._triangles:
            self._polygon_triangles.setdefault(polygon, []).extend(tri)
        # Scale relative to world-space mesh extent, never the view distance.
        coordinates = [v for _p, tri in self._triangles for v in tri]
        extent = max((max(v[i] for v in coordinates) - min(v[i] for v in coordinates) for i in range(3)), default=1)
        self._epsilon = max(1e-7, extent * 1e-6)
        self._geometry_dirty, self._view_key = False, None

    def _project(self):
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        key = (tuple(v for row in self._rv3d.perspective_matrix for v in row), self._region.width, self._region.height)
        if key != self._view_key:
            self._points = tuple(location_3d_to_region_2d(self._region, self._rv3d, center) for center in self._centers)
            self._bins = ScreenBins(self._points)
            self._view_key = key
            self._visibility = {}

    def _raycast(self, origin, direction):
        tree, owners = self._trees[0]
        location, _normal, triangle, _distance = tree.ray_cast(origin, direction)
        return (owners[triangle], location) if location is not None else None

    def _visible_candidates(self):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        self._project()
        result = []
        for index in self._bins.circle(self._cursor, self._radius):
            if index in self._visibility:
                if self._visibility[index]:
                    result.append(index)
                continue
            point = self._points[index]
            center = self._centers[index]
            view_depth = -(self._rv3d.view_matrix @ center).z
            near, far = self._space.clip_start, self._space.clip_end
            camera_view = self._rv3d.view_perspective == 'CAMERA'
            camera = getattr(self._space, 'camera', None)
            if camera_view and camera is not None:
                near, far = camera.data.clip_start, camera.data.clip_end
            clipped = (not near <= view_depth <= far if self._rv3d.is_perspective or camera_view
                       else abs(view_depth) > far)
            if clipped:
                self._visibility[index] = False
                continue
            # Bound orthographic origins to scene extent for numerical stability.
            origin = region_2d_to_origin_3d(self._region, self._rv3d, point,
                                          clamp=max(1.0, self._space.clip_end))
            direction = region_2d_to_vector_3d(self._region, self._rv3d, point)
            depth = (center-origin).dot(direction)
            if depth <= 0:
                continue
            self._visibility[index] = visible_center(center, origin, direction, self._raycast,
                                                     (True, index), self._epsilon)
            if self._visibility[index]:
                result.append(index)
        # Surface samples make partially visible and large faces selectable even
        # when their center lies outside the brush or behind an occluder.
        # The combined scene BVH always picks the first surface, never through.
        selected = set(result)
        x, y = self._cursor
        spacing = max(8.0, self._radius / 12.0)
        steps = math.ceil(self._radius / spacing)
        samples = [(x, y)]
        samples.extend((x+dx*spacing, y+dy*spacing)
                       for dx in range(-steps, steps+1)
                       for dy in range(-steps, steps+1)
                       if (dx*spacing)**2+(dy*spacing)**2 <= self._radius**2)
        for point in samples:
            if not (0 <= point[0] < self._region.width and 0 <= point[1] < self._region.height):
                continue
            origin = region_2d_to_origin_3d(self._region, self._rv3d, point,
                                          clamp=max(1.0, self._space.clip_end))
            direction = region_2d_to_vector_3d(self._region, self._rv3d, point)
            hit = self._raycast(origin, direction)
            if hit is None or not hit[0][0]:
                continue
            depth = -(self._rv3d.view_matrix @ hit[1]).z
            if self._rv3d.is_perspective:
                if not self._space.clip_start <= depth <= self._space.clip_end:
                    continue
            elif abs(depth) > self._space.clip_end:
                continue
            selected.add(hit[0][1])
        return tuple(sorted(selected))

    def modal(self, context, event):
        try:
            if not self._valid(context):
                self._cleanup(restore=True)
                return {'CANCELLED'}
            if event.type == 'ESC' and event.value == 'PRESS':
                self._cleanup(restore=True)
                return {'CANCELLED'}
            if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
                if data.signature(self._mesh) != self._topology:
                    raise ZoneError('Topology changed during selection. Clear selections and reselect faces.')
                if data.read_owners(self._mesh) != self._start:
                    raise ZoneError('Face ownership changed outside the selector. Restart Edit Selection.')
                data.write_owners(self._obj, self._owners)
                self._cleanup(restore=False)
                return {'FINISHED'}
            if self._geometry_dirty:
                if data.signature(self._mesh) != self._topology:
                    raise ZoneError('Topology changed during selection. Clear selections and reselect faces.')
                if data.read_owners(self._mesh) != self._start:
                    raise ZoneError('Face ownership changed outside the selector. Restart Edit Selection.')
                self._rebuild(context)
            x, y = event.mouse_x - self._region.x, event.mouse_y - self._region.y
            inside = 0 <= x < self._region.width and 0 <= y < self._region.height
            self._cursor = (x, y)
            if not inside:
                self._drag = False
                return {'PASS_THROUGH'}
            if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                self._radius = min(500, max(4, self._radius * (1.1 if event.type == 'WHEELUPMOUSE' else 1/1.1)))
                self._hover = self._visible_candidates()
                self._area.tag_redraw()
                return {'RUNNING_MODAL'}
            elif event.type == 'LEFTMOUSE':
                self._drag = event.value == 'PRESS'
            elif event.type != 'MOUSEMOVE':
                if event.type == 'MIDDLEMOUSE' or event.type.startswith('NDOF_'):
                    self._drag = False
                self._area.tag_redraw()
                return {'PASS_THROUGH'}
            self._hover = self._visible_candidates()
            if self._drag:
                owners = assign_faces(self._owners, self._hover, self._token, remove=event.shift)
                if owners != self._owners:
                    self._owners = owners
                    self._gpu_faces = None
            self._area.tag_redraw()
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self._cleanup(restore=True)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def _draw_world(self):
        if self._closed or bpy.context.area != self._area or bpy.context.region != self._region:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        try:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            gpu.state.depth_test_set('LESS_EQUAL')
            gpu.state.blend_set('ALPHA')
            if self._gpu_batches is None:
                self._gpu_batches = (
                    batch_for_shader(shader, 'TRIS', {'pos': self._occluders}),
                    batch_for_shader(shader, 'POINTS', {'pos': self._centers}))
            if self._gpu_faces is None:
                faces = [v for polygon, token in enumerate(self._owners) if token == self._token
                         for v in self._polygon_triangles.get(polygon, ())]
                self._gpu_faces = batch_for_shader(shader, 'TRIS', {'pos': faces}) if faces else False
            occluders, centers = self._gpu_batches
            shader.bind()
            # Write our own opaque-surface depth, even in Blender's X-Ray view.
            # Transparent color preserves the artist's existing viewport.
            gpu.state.depth_mask_set(True)
            shader.uniform_float('color', (0, 0, 0, 0))
            occluders.draw(shader)
            gpu.state.depth_mask_set(False)
            # Lift only the drawing toward the viewer. Picking and the scene
            # depth prepass keep the exact surface, including in X-Ray view.
            # View-facing bias works for reversed winding and both projections.
            from mathutils import Vector
            toward_view = self._rv3d.view_matrix.inverted().to_3x3() @ Vector((0, 0, 1))
            lift = toward_view.normalized() * (self._epsilon * 4)
            with gpu.matrix.push_pop():
                gpu.matrix.translate(lift)
                if self._gpu_faces:
                    shader.uniform_float('color', (0.08, 0.58, 1.0, 0.45))
                    self._gpu_faces.draw(shader)
                gpu.state.point_size_set(4)
                shader.uniform_float('color', (0.95, 0.95, 1.0, 1.0))
                centers.draw(shader)
                if self._hover:
                    gpu.state.point_size_set(8)
                    shader.uniform_float('color', (0.15, 0.85, 1.0, 1.0))
                    batch_for_shader(shader, 'POINTS', {'pos': [self._centers[i] for i in self._hover]}).draw(shader)
        except Exception:
            self._cleanup(restore=True)
            raise
        finally:
            gpu.state.point_size_set(1)
            gpu.state.blend_set('NONE')
            gpu.state.depth_mask_set(True)
            gpu.state.depth_test_set('NONE')

    def _draw_circle(self):
        if self._closed or bpy.context.area != self._area or bpy.context.region != self._region:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        try:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            x, y = self._cursor
            points = [(x+self._radius*math.cos(i*math.tau/64), y+self._radius*math.sin(i*math.tau/64), 0) for i in range(65)]
            shader.bind()
            shader.uniform_float('color', (0.2, 0.75, 1, 1))
            batch_for_shader(shader, 'LINE_STRIP', {'pos': points}).draw(shader)
        except Exception:
            self._cleanup(restore=True)
            raise

    def cancel(self, _context):
        self._cleanup(restore=True)

    def _cleanup(self, *, restore):
        if getattr(self, '_closed', True):
            return
        self._closed = True
        try:
            # Selection is staged in Python: Esc has never mutated the mesh.
            # External lifecycle changes therefore cannot overwrite new faces.
            if self._area.type == 'VIEW_3D':
                self._area.header_text_set(None)
                self._area.tag_redraw()
        except (ReferenceError, AttributeError):
            pass
        finally:
            for modifier, viewport in getattr(self, '_modifier_states', ()):
                try:
                    modifier.show_viewport = viewport
                except ReferenceError:
                    pass
            self._modifier_states = []
            for handle in self._handles:
                try:
                    bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
                except (ValueError, ReferenceError):
                    pass
            self._handles.clear()
            validation_state.remove_depsgraph_observer(self._changed)
            _sessions.discard(self)
            self._trees, self._centers, self._triangles, self._hover = (), (), (), ()
            self._gpu_batches = self._occluders = self._bins = self._points = None
            self._gpu_faces = self._polygon_triangles = None
            self._visibility = None
            self._start = self._owners = None
            self._obj = self._mesh = self._context = None
            self._area = self._region = self._rv3d = self._space = None


@validation_state.persistent
def _load_pre(*_args):
    cleanup_all()


def cleanup_all():
    for session in tuple(_sessions):
        session._cleanup(restore=True)


def register():
    cleanup_all()
    handlers = getattr(bpy.app.handlers, 'load_pre', None)
    if handlers is not None:
        for handler in list(handlers):
            if getattr(handler, '_clothnext_zone_load', False):
                handlers.remove(handler)
    if handlers is not None and _load_pre not in handlers:
        handlers.append(_load_pre)


def unregister():
    cleanup_all()
    handlers = getattr(bpy.app.handlers, 'load_pre', None)
    if handlers is not None and _load_pre in handlers:
        handlers.remove(_load_pre)


_load_pre._clothnext_zone_load = True


CLASSES = (CLOTHNEXT_OT_edit_material_zone_selection,)
