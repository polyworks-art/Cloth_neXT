# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Opt-in experimental low-poly proxies for deforming Collider animation."""

from __future__ import annotations

from dataclasses import dataclass

import bpy

from ..bake.controller import shared_controller

PROXY_COLLECTION = "Cloth NeXt Proxies"
PROXY_MARKER = "cloth_next_experimental_collider_proxy"
PROXY_SOURCE = "cloth_next_proxy_source"
# Observed PPF 0.11 peak while cbor2 expands nested float lists, converts them
# to float64 arrays, and serializes build state. Deliberately conservative.
PPF_PEAK_BYTES_PER_VERTEX_SAMPLE = 300

_DEFORMATION_MODIFIERS = {
    "ARMATURE", "CAST", "CORRECTIVE_SMOOTH", "CURVE", "DISPLACE",
    "HOOK", "LAPLACIANDEFORM", "LAPLACIANSMOOTH", "LATTICE",
    "MESH_DEFORM", "SHRINKWRAP", "SIMPLE_DEFORM", "SMOOTH",
    "SURFACE_DEFORM", "WARP", "WAVE",
}


class ColliderProxyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ColliderProxyEstimate:
    source_vertices: int
    proxy_vertices: int
    sample_count: int
    source_peak_bytes: int
    proxy_peak_bytes: int


def motion_sample_count(frame_start: int, frame_end: int,
                        samples_per_frame: int) -> int:
    intervals = max(0, int(frame_end) - int(frame_start))
    return intervals * max(1, int(samples_per_frame)) + 1


def estimated_ppf_peak_bytes(vertex_count: int, sample_count: int) -> int:
    return (max(0, int(vertex_count)) * max(0, int(sample_count)) *
            PPF_PEAK_BYTES_PER_VERTEX_SAMPLE)


def proxy_estimate(source, proxy=None) -> ColliderProxyEstimate:
    settings = source.cloth_next
    source_vertices = int(getattr(settings,
                                  "collider_proxy_source_vertices", 0) or 0)
    if source_vertices <= 0:
        source_vertices = len(getattr(getattr(source, "data", None),
                                      "vertices", ()))
    proxy_vertices = int(getattr(settings,
                                 "collider_proxy_result_vertices", 0) or 0)
    if proxy is not None:
        proxy_vertices = len(getattr(getattr(proxy, "data", None),
                                     "vertices", ()))
    samples = motion_sample_count(settings.bake_start, settings.bake_end,
                                  settings.collider_samples_per_frame)
    return ColliderProxyEstimate(
        source_vertices, proxy_vertices, samples,
        estimated_ppf_peak_bytes(source_vertices, samples),
        estimated_ppf_peak_bytes(proxy_vertices, samples))


def format_bytes(value: int) -> str:
    if value >= 1024 ** 3:
        return f"{value / float(1024 ** 3):.1f} GB"
    return f"{value / float(1024 ** 2):.0f} MB"


def is_generated_proxy(obj) -> bool:
    try:
        return bool(obj.get(PROXY_MARKER, False))
    except (AttributeError, TypeError):
        return False


def proxy_source(obj):
    settings = getattr(obj, "cloth_next", None)
    return getattr(settings, "collider_proxy_source", None) if settings else None


def resolve_proxy(source):
    settings = source.cloth_next
    if not bool(getattr(settings, "collider_proxy_enabled", False)):
        return source
    proxy = getattr(settings, "collider_proxy_object", None)
    if proxy is None:
        raise ColliderProxyError(
            f'{source.name}: Experimental Collider Proxy is enabled, but no '
            'generated Proxy is assigned. Generate or disable the Proxy.')
    if getattr(proxy, "type", "") != "MESH" or not is_generated_proxy(proxy):
        raise ColliderProxyError(
            f'{source.name}: the assigned Experimental Collider Proxy is not '
            'a valid generated Mesh.')
    if proxy_source(proxy) is not source:
        raise ColliderProxyError(
            f'{source.name}: the assigned Experimental Collider Proxy belongs '
            'to another source. Regenerate it.')
    return proxy


def _copy_collider_settings(source, proxy) -> None:
    source_settings = source.cloth_next
    target = proxy.cloth_next
    target.enabled = True
    target.role = "COLLIDER"
    target.collider_motion = "ANIMATED"
    target.collider_samples_per_frame = int(
        source_settings.collider_samples_per_frame)
    target.bake_start = int(source_settings.bake_start)
    target.bake_end = int(source_settings.bake_end)
    for name in ("surface_grip", "collision_gap", "surface_offset"):
        setattr(target.collision, name, getattr(source_settings.collision, name))
    target.collider_proxy_source = source


def sync_proxy_settings(source, proxy) -> None:
    """Keep solver-facing contact and range settings owned by the source."""
    _copy_collider_settings(source, proxy)


def _proxy_collection(scene):
    collection = bpy.data.collections.get(PROXY_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(PROXY_COLLECTION)
        scene.collection.children.link(collection)
    return collection


def _remove_owned_proxy(source) -> None:
    proxy = getattr(source.cloth_next, "collider_proxy_object", None)
    if proxy is None or not is_generated_proxy(proxy):
        return
    if proxy_source(proxy) is not source:
        return
    mesh = getattr(proxy, "data", None)
    bpy.data.objects.remove(proxy, do_unlink=True)
    if mesh is not None and getattr(mesh, "users", 1) == 0:
        bpy.data.meshes.remove(mesh)


def _apply_modifier(context, obj, modifier_name: str) -> None:
    override = dict(object=obj, active_object=obj, selected_objects=[obj],
                    selected_editable_objects=[obj])
    with context.temp_override(**override):
        result = bpy.ops.object.modifier_apply(modifier=modifier_name)
    if "FINISHED" not in result:
        raise ColliderProxyError(
            f"Blender could not apply Proxy modifier {modifier_name!r}.")


def _reduce_to_vertex_target(context, obj, target: int) -> None:
    """Reduce ``obj`` until its vertex count reaches the requested ceiling.

    Blender's Decimate ratio controls faces, not vertices.  A single ratio
    derived from the vertex count can consequently leave a proxy far above
    the value shown in the UI.  Correct the remaining error with bounded
    follow-up passes; stop if Blender can no longer make progress.
    """
    previous = len(obj.data.vertices)
    for pass_number in range(4):
        if previous <= target:
            return
        decimate = obj.modifiers.new(
            "Cloth NeXt Proxy Reduction" if pass_number == 0 else
            f"Cloth NeXt Proxy Reduction {pass_number + 1}", "DECIMATE")
        decimate.decimate_type = "COLLAPSE"
        decimate.ratio = max(0.001, min(1.0, target / float(previous)))
        decimate.use_collapse_triangulate = True
        obj.modifiers.move(len(obj.modifiers) - 1, 0)
        _apply_modifier(context, obj, decimate.name)
        current = len(obj.data.vertices)
        if current >= previous:
            return
        previous = current


def generate_proxy(context, source):
    settings = source.cloth_next
    if (getattr(source, "type", "") != "MESH" or
            not settings.enabled or settings.role != "COLLIDER" or
            settings.collider_motion != "ANIMATED"):
        raise ColliderProxyError(
            "Experimental Collider Proxies require an enabled, animated "
            "Mesh Collider.")
    original_frame = int(context.scene.frame_current)
    created = None
    try:
        context.scene.frame_set(int(settings.bake_start))
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = source.evaluated_get(depsgraph)
        evaluated_mesh = evaluated.to_mesh()
        try:
            source_vertices = len(evaluated_mesh.vertices)
            if source_vertices < 4 or len(evaluated_mesh.polygons) == 0:
                raise ColliderProxyError(
                    f"{source.name}: evaluated source has no usable surface.")
        finally:
            evaluated.to_mesh_clear()

        _remove_owned_proxy(source)
        # Object.copy preserves vertex groups, parenting, object animation,
        # and the complete modifier stack. A private Mesh copy keeps all
        # edits isolated from the render source.
        created = source.copy()
        created.data = source.data.copy()
        created.name = f"{source.name}_CNX_Proxy"
        created.data.name = f"{source.data.name}_CNX_Proxy"
        _proxy_collection(context.scene).objects.link(created)
        created.display_type = "WIRE"
        created.hide_render = True
        created.show_name = True
        created[PROXY_MARKER] = True
        created[PROXY_SOURCE] = source.name

        # Keep only topology-preserving deformation. Subdivision, Geometry
        # Nodes, Boolean, Solidify, Array, and similar render/detail modifiers
        # are precisely what the simulation proxy must avoid.
        for modifier in tuple(created.modifiers):
            if modifier.type not in _DEFORMATION_MODIFIERS:
                created.modifiers.remove(modifier)

        base_vertices = len(created.data.vertices)
        target = min(max(500, int(settings.collider_proxy_target_vertices)),
                     base_vertices)
        if target < base_vertices:
            if getattr(created.data, "shape_keys", None) is not None:
                raise ColliderProxyError(
                    f"{source.name}: the base Mesh has Shape Keys and cannot "
                    "be destructively decimated. Use at least "
                    f"{base_vertices:,} target vertices or provide a manual "
                    "low-poly Collider.")
            _reduce_to_vertex_target(context, created, target)

        _copy_collider_settings(source, created)
        created.cloth_next.collider_proxy_enabled = False
        created.cloth_next.collider_proxy_object = None

        settings.collider_proxy_object = created
        settings.collider_proxy_enabled = True
        settings.collider_proxy_source_vertices = source_vertices
        settings.collider_proxy_result_vertices = len(created.data.vertices)
        return created
    except Exception:
        if created is not None and created.name in bpy.data.objects:
            mesh = created.data
            bpy.data.objects.remove(created, do_unlink=True)
            if mesh is not None and getattr(mesh, "users", 1) == 0:
                bpy.data.meshes.remove(mesh)
        raise
    finally:
        context.scene.frame_set(original_frame)
        context.view_layer.update()


class CLOTHNEXT_OT_generate_collider_proxy(bpy.types.Operator):
    """Generate or replace an experimental animated Collider proxy"""

    bl_idname = "clothnext.generate_collider_proxy"
    bl_label = "Generate Experimental Proxy"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        settings = getattr(obj, "cloth_next", None) if obj else None
        return bool(obj is not None and obj.type == "MESH" and settings and
                    settings.enabled and settings.role == "COLLIDER" and
                    settings.collider_motion == "ANIMATED" and
                    not is_generated_proxy(obj) and
                    not shared_controller.snapshot().active)

    def execute(self, context):
        try:
            proxy = generate_proxy(context, context.active_object)
        except (ColliderProxyError, RuntimeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"},
                    f"Experimental Collider Proxy '{proxy.name}' generated.")
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_generate_collider_proxy,)
