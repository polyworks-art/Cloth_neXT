# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh-owned hard Material Zone authoring; scans only in explicit operations."""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import bpy

from ..bake.controller import shared_controller
from ..materials.friction import artist_friction_to_ppf, PPF_FRICTION_SCALE
from ..materials.zones import MaterialZone, PARAMETERS, ZoneError, topology_signature, triangle_tables
from ..ppf.schema.params import shell_wire_params
from . import object_properties, validation_state

ATTRIBUTE = "cloth_next_material_zone"
MESH_ID = "cloth_next_zone_mesh_id"
TOPOLOGY = "cloth_next_zone_topology"
NEXT_TOKEN = "cloth_next_zone_next_token"


def zones_of(obj):
    return tuple(MaterialZone(z.identity, int(z.token), z.name, bool(z.enabled),
                             tuple((p.key, float(p.value) * PPF_FRICTION_SCALE
                                    if p.key == 'friction' else float(p.value))
                                   for p in z.overrides))
                 for z in getattr(obj.cloth_next, 'material_zones', ()))


def signature(mesh):
    return topology_signature(len(mesh.vertices), (tuple(p.vertices) for p in mesh.polygons))


def attribute(mesh):
    attr = mesh.attributes.get(ATTRIBUTE)
    if attr is None or attr.domain != 'FACE' or attr.data_type != 'INT':
        raise ZoneError("Material Zone FACE/INT attribute is missing or damaged. Clear selections and reselect faces.")
    return attr


def read_owners(mesh):
    attr = attribute(mesh)
    if len(attr.data) != len(mesh.polygons):
        raise ZoneError("Material Zone face count changed. Clear selections and reselect faces.")
    return tuple(int(item.value) for item in attr.data)


def initialize(obj):
    mesh, settings = obj.data, obj.cloth_next
    if mesh.users > 1 or mesh.library or obj.library:
        raise ZoneError(f"{obj.name}: make the mesh local and single-user before editing Material Zones.")
    if not getattr(settings, 'material_zones', ()):
        mesh[MESH_ID] = str(uuid4())
        settings.material_zone_mesh_id = mesh[MESH_ID]
        mesh[TOPOLOGY] = signature(mesh)
        mesh[NEXT_TOKEN] = 1
        attr = mesh.attributes.get(ATTRIBUTE)
        if attr:
            mesh.attributes.remove(attr)
        mesh.attributes.new(ATTRIBUTE, 'INT', 'FACE')
    validate_ownership(obj)


def storage_owners(obj):
    settings, mesh = obj.cloth_next, obj.data
    if not getattr(settings, 'material_zones', ()):
        return ()
    label = ', '.join(z.name for z in settings.material_zones)
    if (settings.material_zone_mesh_id != mesh.get(MESH_ID)
            or mesh.get(TOPOLOGY) != signature(mesh)):
        settings.material_zone_status = 'Topology changed · clear selections and reselect faces'
        raise ZoneError(f"{obj.name}, Material Zones {label}: topology or mesh changed. Clear zone selections and reselect faces.")
    try:
        return read_owners(mesh)
    except ZoneError as exc:
        raise ZoneError(f'{obj.name}, Material Zones {label}: {exc}') from exc


def validate_ownership(obj):
    if not getattr(obj.cloth_next, 'material_zones', ()):
        return ()
    owners = storage_owners(obj)
    triangle_tables(obj.name, owners, zones_of(obj), (), {}, ())
    obj.cloth_next.material_zone_status = ''
    return owners


def update_counts(obj, owners, *, dirty=True):
    counts = {}
    for token in owners:
        counts[token] = counts.get(token, 0) + 1
    for zone in obj.cloth_next.material_zones:
        zone.face_count = counts.get(zone.token, 0)
    obj.cloth_next.material_zone_digest = hashlib.sha256(
        json.dumps(owners, separators=(',', ':')).encode()).hexdigest()
    if dirty:
        validation_state.mark_settings_dirty(obj)
        validation_state.mark_geometry_dirty(obj)


def write_owners(obj, owners):
    if obj.data.users > 1 or obj.data.library or obj.library:
        raise ZoneError(f'{obj.name}: make the mesh local and single-user before changing face ownership.')
    if len(owners) != len(obj.data.polygons):
        raise ZoneError(f'{obj.name}: face ownership count changed. Clear selections and reselect faces.')
    attribute(obj.data).data.foreach_set('value', owners)
    update_counts(obj, owners)
    obj.data.update()


def settings_record(obj):
    """Cheap fingerprint: definitions plus authoring-time ownership digest."""
    zones = zones_of(obj)
    if not zones:
        return None
    return {'zones': sorted((z.identity, z.token, z.name, z.enabled, sorted(z.overrides))
                            for z in zones),
            'mesh': obj.cloth_next.material_zone_mesh_id,
            'ownership': obj.cloth_next.material_zone_digest}


def export_tables(obj, triangles, shell, friction=()):
    if not getattr(obj.cloth_next, 'material_zones', ()):
        return None
    owners = validate_ownership(obj)
    mesh = obj.data
    mesh.calc_loop_triangles()
    source = tuple(mesh.loop_triangles)
    if len(source) != len(triangles) or any(tuple(t.vertices) != tuple(v) for t, v in zip(source, triangles)):
        raise ZoneError(f"{obj.name}: Material Zones cannot map modified topology. Apply topology modifiers, clear selections and reselect faces.")
    legacy = tuple(artist_friction_to_ppf(value) for value in friction)
    tables = triangle_tables(obj.name, owners, zones_of(obj),
                             tuple(t.polygon_index for t in source), shell_wire_params(shell), legacy)
    update_counts(obj, owners, dirty=False)
    return tables or None


def require_capability(obj, resolved):
    if not getattr(obj.cloth_next, 'material_zones', ()):
        return
    from ..ppf.resolver import SolverMode
    from ..ppf.official_scene_bridge import uses_official_bridge
    from ..ppf.compatibility import protocol_profile
    profile = protocol_profile(str(getattr(resolved, 'protocol_version', '')),
                               str(getattr(resolved, 'schema_version', '')))
    if (resolved.mode is not SolverMode.MANAGED_INSTALLATION or profile is None
            or (not uses_official_bridge(resolved)
                and profile.adapter_id not in {'legacy-schema2', 'modern-schema2'})):
        names = ', '.join(z.name for z in obj.cloth_next.material_zones)
        raise ZoneError(f"{obj.name}, Material Zones {names}: select a supported managed Cloth NeXt solver or remove the zones. Hard triangle materials are required.")


class ZoneOperator:
    @classmethod
    def poll(cls, context):
        obj = getattr(context, 'object', None)
        return bool(obj and obj.type == 'MESH' and obj.mode == 'OBJECT'
                    and getattr(obj, 'cloth_next', None) and obj.cloth_next.role == 'CLOTH'
                    and not shared_controller.snapshot().active)

    def execute(self, context):
        if not self.poll(context):
            self.report({'ERROR'}, 'Material Zone authoring requires Object Mode and an idle Bake.')
            return {'CANCELLED'}
        try:
            self.apply(context.object)
        except ZoneError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        validation_state.mark_settings_dirty(context.object)
        return {'FINISHED'}


def find_zone(obj, identity):
    zone = next((z for z in obj.cloth_next.material_zones if z.identity == identity), None)
    if zone is None:
        raise ZoneError(f'{obj.name}: Material Zone no longer exists. Add a zone.')
    return zone


class CLOTHNEXT_OT_add_material_zone(ZoneOperator, bpy.types.Operator):
    bl_idname = 'clothnext.add_material_zone'
    bl_label = 'Add Material Zone'
    bl_options = {'UNDO', 'INTERNAL'}

    def apply(self, obj):
        initialize(obj)
        zone = obj.cloth_next.material_zones.add()
        zone.identity = str(uuid4())
        zone.token = int(obj.data[NEXT_TOKEN])
        obj.data[NEXT_TOKEN] = zone.token + 1
        zone.name = 'Material Zone'
        update_counts(obj, read_owners(obj.data))


class CLOTHNEXT_OT_remove_material_zone(ZoneOperator, bpy.types.Operator):
    bl_idname = 'clothnext.remove_material_zone'
    bl_label = 'Remove Material Zone'
    bl_options = {'UNDO', 'INTERNAL'}
    identity: bpy.props.StringProperty(options={'HIDDEN'})

    def apply(self, obj):
        zone = find_zone(obj, self.identity)
        owners = storage_owners(obj)
        write_owners(obj, tuple(0 if token == zone.token else token for token in owners))
        index = next(i for i, z in enumerate(obj.cloth_next.material_zones) if z.identity == self.identity)
        obj.cloth_next.material_zones.remove(index)


class CLOTHNEXT_OT_clear_material_zone_selections(ZoneOperator, bpy.types.Operator):
    bl_idname = 'clothnext.clear_material_zone_selections'
    bl_label = 'Clear All Zone Selections'
    bl_options = {'UNDO', 'INTERNAL'}

    def apply(self, obj):
        mesh = obj.data
        if mesh.users > 1 or mesh.library or obj.library:
            raise ZoneError('Make the mesh local and single-user first.')
        attr = mesh.attributes.get(ATTRIBUTE)
        if attr:
            mesh.attributes.remove(attr)
        mesh.attributes.new(ATTRIBUTE, 'INT', 'FACE')
        mesh[MESH_ID] = str(uuid4())
        obj.cloth_next.material_zone_mesh_id = mesh[MESH_ID]
        mesh[TOPOLOGY] = signature(mesh)
        mesh[NEXT_TOKEN] = max((z.token for z in obj.cloth_next.material_zones), default=0) + 1
        obj.cloth_next.material_zone_status = ''
        write_owners(obj, (0,) * len(mesh.polygons))


class CLOTHNEXT_OT_material_zone_property(ZoneOperator, bpy.types.Operator):
    bl_idname = 'clothnext.material_zone_property'
    bl_label = 'Material Zone Property'
    bl_options = {'UNDO', 'INTERNAL'}
    identity: bpy.props.StringProperty(options={'HIDDEN'})
    key: bpy.props.StringProperty(options={'HIDDEN'})
    remove: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        if self.key:
            return self.execute(context)
        if not self.poll(context):
            return {'CANCELLED'}
        zone = find_zone(context.object, self.identity)
        present = {p.key for p in zone.overrides}
        identity = self.identity
        def menu(menu_self, _context):
            for key, (label, _low, _high) in PARAMETERS.items():
                if key not in present:
                    op = menu_self.layout.operator(self.bl_idname, text=label)
                    op.identity, op.key = identity, key
        context.window_manager.popup_menu(menu, title='Add Property')
        return {'FINISHED'}

    def apply(self, obj):
        zone = find_zone(obj, self.identity)
        if not self.remove and self.key not in PARAMETERS:
            raise ZoneError('Unsupported Material Zone property. Remove it.')
        index = next((i for i, p in enumerate(zone.overrides) if p.key == self.key), None)
        if self.remove:
            if index is not None:
                zone.overrides.remove(index)
        elif index is None:
            values = shell_wire_params(object_properties.shell_settings_from(obj.cloth_next))
            override = zone.overrides.add()
            override.key = self.key
            override.value = values[self.key] / PPF_FRICTION_SCALE if self.key == 'friction' else values[self.key]


def draw(layout, context):
    settings = context.object.cloth_next
    layout.separator()
    column = layout.column(align=True)
    column.enabled = not shared_controller.snapshot().active
    column.label(text='Material Zones')
    column.operator('clothnext.add_material_zone', icon='ADD')
    for zone in settings.material_zones:
        box = column.box()
        row = box.row(align=True)
        row.prop(zone, 'expanded', text='', emboss=False,
                 icon='TRIA_DOWN' if zone.expanded else 'TRIA_RIGHT')
        row.prop(zone, 'enabled', text='')
        row.prop(zone, 'name', text='')
        row.label(text=f'{zone.face_count} Faces')
        row.operator('clothnext.edit_material_zone_selection', text='Edit Selection').identity = zone.identity
        row.operator('clothnext.remove_material_zone', text='', icon='X').identity = zone.identity
        if zone.expanded:
            for override in zone.overrides:
                row = box.row(align=True)
                row.prop(override, 'value', text=PARAMETERS.get(override.key, (override.key,))[0])
                op = row.operator('clothnext.material_zone_property', text='', icon='X')
                op.identity, op.key, op.remove = zone.identity, override.key, True
            box.operator('clothnext.material_zone_property', text='Add Property', icon='ADD').identity = zone.identity
    if settings.material_zones:
        column.label(text='Managed solver required · hard face boundaries', icon='INFO')
        if settings.material_zone_status:
            column.label(text=settings.material_zone_status, icon='ERROR')
        column.operator('clothnext.clear_material_zone_selections')


CLASSES = (CLOTHNEXT_OT_add_material_zone, CLOTHNEXT_OT_remove_material_zone,
           CLOTHNEXT_OT_clear_material_zone_selections, CLOTHNEXT_OT_material_zone_property)
