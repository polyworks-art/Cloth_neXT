# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-destructive material integration of the TETHYS wetness image sequence.

Each polygon owns one pixel of an EXR image sequence (R surface water mm,
G saturation). The polygon finds its pixel through the FACE attribute
``cnx_tethys_cell`` (polygon index + 1; 0 means "no TETHYS data"). Blender
plays the sequence itself, so scrubbing, viewport, F12, animation and
background renders need no Python per frame, and face boundaries stay sharp.

Around every Principled BSDF the original Base Color / Roughness / Coat
Weight sources are captured and routed through ``CNX TETHYS Wetness``. All
added nodes are tagged so Detach restores the exact previous links and values.
"""
from __future__ import annotations

import json

import bpy
import numpy as np

GROUP = "CNX TETHYS Wetness"
CELL_GROUP = "CNX TETHYS Cell"
ATTRIBUTE = "cnx_tethys_cell"
OWNED = "cnx_tethys_owned"
RECORD = "cnx_tethys_record"
IMAGE_PREFIX = "CNX TETHYS "


class ShaderError(RuntimeError):
    pass


# ------------------------------------------------------------ node groups

def _socket(tree, name, in_out, socket_type, default=None, minimum=None, maximum=None):
    item = tree.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type)
    if default is not None:
        item.default_value = default
    if minimum is not None:
        item.min_value = minimum
    if maximum is not None:
        item.max_value = maximum
    return item


def _math(tree, operation, a=None, b=None, location=(0, 0), clamp=False, extra=None):
    node = tree.nodes.new("ShaderNodeMath")
    node.operation = operation
    node.use_clamp = clamp
    node.location = location
    for index, value in ((0, a), (1, b), (2, extra)):
        if isinstance(value, (int, float)):
            node.inputs[index].default_value = value
        elif value is not None:
            tree.links.new(value, node.inputs[index])
    return node.outputs[0]


def _mix_float(tree, factor, a, b, location):
    node = tree.nodes.new("ShaderNodeMix")
    node.data_type = "FLOAT"
    node.clamp_factor = True
    node.location = location
    tree.links.new(factor, node.inputs["Factor"])
    for name, value in (("A", a), ("B", b)):
        socket = next(s for s in node.inputs if s.name == name and s.type == "VALUE")
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            tree.links.new(value, socket)
    return next(s for s in node.outputs if s.name == "Result" and s.type == "VALUE")


def cell_group():
    tree = bpy.data.node_groups.get(CELL_GROUP)
    if tree is not None:
        return tree
    tree = bpy.data.node_groups.new(CELL_GROUP, "ShaderNodeTree")
    _socket(tree, "Cell", "INPUT", "NodeSocketFloat", 0.0)
    _socket(tree, "Width", "INPUT", "NodeSocketFloat", 1.0, 1.0)
    _socket(tree, "Height", "INPUT", "NodeSocketFloat", 1.0, 1.0)
    _socket(tree, "Vector", "OUTPUT", "NodeSocketVector")
    _socket(tree, "Valid", "OUTPUT", "NodeSocketFloat")
    inputs = tree.nodes.new("NodeGroupInput")
    outputs = tree.nodes.new("NodeGroupOutput")
    inputs.location, outputs.location = (-600, 0), (600, 0)
    cell, width, height = inputs.outputs["Cell"], inputs.outputs["Width"], inputs.outputs["Height"]
    index = _math(tree, "SUBTRACT", cell, 1.0, (-400, 100))
    rounded = _math(tree, "ROUND", index, None, (-250, 100))
    column = _math(tree, "MODULO", rounded, width, (-100, 200))
    row = _math(tree, "FLOOR", _math(tree, "DIVIDE", rounded, width, (-100, 50)), None, (50, 50))
    u = _math(tree, "DIVIDE", _math(tree, "ADD", column, 0.5, (50, 200)), width, (200, 200))
    v = _math(tree, "DIVIDE", _math(tree, "ADD", row, 0.5, (200, 50)), height, (350, 50))
    combine = tree.nodes.new("ShaderNodeCombineXYZ")
    combine.location = (450, 150)
    tree.links.new(u, combine.inputs[0])
    tree.links.new(v, combine.inputs[1])
    tree.links.new(combine.outputs[0], outputs.inputs["Vector"])
    tree.links.new(_math(tree, "GREATER_THAN", cell, 0.5, (450, -50)), outputs.inputs["Valid"])
    return tree


def wetness_group():
    tree = bpy.data.node_groups.get(GROUP)
    if tree is not None and "Water Normal" in tree.interface.items_tree:
        return tree
    if tree is not None:          # older development layout: rebuild under the same name
        tree.name = GROUP + " (old)"
    tree = bpy.data.node_groups.new(GROUP, "ShaderNodeTree")
    _socket(tree, "Base Color", "INPUT", "NodeSocketColor", (0.8, 0.8, 0.8, 1.0))
    _socket(tree, "Roughness", "INPUT", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _socket(tree, "Coat Weight", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _socket(tree, "Surface Water", "INPUT", "NodeSocketFloat", 0.0, 0.0)
    _socket(tree, "Absorbed Water", "INPUT", "NodeSocketFloat", 0.0, 0.0)
    _socket(tree, "Normal Map", "INPUT", "NodeSocketColor", (0.5, 0.5, 1.0, 1.0))
    _socket(tree, "Valid", "INPUT", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _socket(tree, "Darkening", "INPUT", "NodeSocketFloat", 0.6, 0.0, 1.0)
    _socket(tree, "Wet Roughness", "INPUT", "NodeSocketFloat", 0.08, 0.0, 1.0)
    _socket(tree, "Film Response", "INPUT", "NodeSocketFloat", 0.08, 0.001)
    _socket(tree, "Moisture Response", "INPUT", "NodeSocketFloat", 0.25, 0.001)
    _socket(tree, "Strength", "INPUT", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _socket(tree, "Base Color", "OUTPUT", "NodeSocketColor")
    _socket(tree, "Roughness", "OUTPUT", "NodeSocketFloat")
    _socket(tree, "Coat Weight", "OUTPUT", "NodeSocketFloat")
    _socket(tree, "Film", "OUTPUT", "NodeSocketFloat")
    _socket(tree, "Moisture", "OUTPUT", "NodeSocketFloat")
    _socket(tree, "Water Height", "OUTPUT", "NodeSocketFloat")
    _socket(tree, "Water Normal", "OUTPUT", "NodeSocketColor")
    i = tree.nodes.new("NodeGroupInput")
    o = tree.nodes.new("NodeGroupOutput")
    i.location, o.location = (-900, 0), (700, 0)
    gate = _math(tree, "MULTIPLY", i.outputs["Valid"], i.outputs["Strength"], (-700, -300))
    film = _math(tree, "MULTIPLY", _math(tree, "DIVIDE", i.outputs["Surface Water"], i.outputs["Film Response"],
                                         (-700, 100), clamp=True), gate, (-500, 100), clamp=True)
    # Darkening follows the water actually held in the fibres (mm), so a
    # waterproof coat with almost no capacity never darkens like cotton.
    moisture = _math(tree, "MULTIPLY", _math(tree, "DIVIDE", i.outputs["Absorbed Water"],
                                             i.outputs["Moisture Response"], (-700, -100), clamp=True),
                     gate, (-500, -100), clamp=True)
    # Moisture darkens and deepens the original color: mix(c, c*c, k).
    darken = _math(tree, "MULTIPLY", moisture, i.outputs["Darkening"], (-300, 300))
    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.clamp_factor = True
    mix.location = (-100, 300)
    tree.links.new(darken, mix.inputs["Factor"])
    color_a = next(s for s in mix.inputs if s.name == "A" and s.type == "RGBA")
    color_b = next(s for s in mix.inputs if s.name == "B" and s.type == "RGBA")
    tree.links.new(i.outputs["Base Color"], color_a)
    tree.links.new(i.outputs["Base Color"], color_b)
    tree.links.new(next(s for s in mix.outputs if s.type == "RGBA"), o.inputs["Base Color"])
    # Moisture slightly smooths; a film turns the surface wet-glossy.
    damp = _mix_float(tree, _math(tree, "MULTIPLY", moisture, 0.25, (-300, 0)), i.outputs["Roughness"],
                      _math(tree, "MULTIPLY", i.outputs["Roughness"], 0.6, (-300, -150)), (-100, 0))
    rough = _mix_float(tree, film, damp, i.outputs["Wet Roughness"], (150, 0))
    tree.links.new(rough, o.inputs["Roughness"])
    coat = _mix_float(tree, film, i.outputs["Coat Weight"], 1.0, (150, -250))
    tree.links.new(coat, o.inputs["Coat Weight"])
    tree.links.new(film, o.inputs["Film"])
    tree.links.new(moisture, o.inputs["Moisture"])
    # Surface water (mm) as relief for beads and rivulets; zero where invalid.
    tree.links.new(_math(tree, "MULTIPLY", i.outputs["Surface Water"], gate, (150, -450)), o.inputs["Water Height"])
    # Tangent-space normal of the water surface (drops, rivulets) as a
    # normal-map colour; flat where there is no data.
    flat = tree.nodes.new("ShaderNodeMix")
    flat.data_type = "RGBA"
    flat.clamp_factor = True
    flat.location = (400, -600)
    tree.links.new(gate, flat.inputs["Factor"])
    next(s for s in flat.inputs if s.name == "A" and s.type == "RGBA").default_value = (0.5, 0.5, 1.0, 1.0)
    tree.links.new(i.outputs["Normal Map"], next(s for s in flat.inputs if s.name == "B" and s.type == "RGBA"))
    tree.links.new(next(s for s in flat.outputs if s.type == "RGBA"), o.inputs["Water Normal"])
    return tree


# --------------------------------------------------------------- per object

def image_name(obj) -> str:
    return f"{IMAGE_PREFIX}{obj.name}"


def normal_image_name(obj) -> str:
    return f"{IMAGE_PREFIX}{obj.name} Normal"


def _restore_validation_after(obj, write):
    """Writing the cell attribute is not a geometry change for the cloth
    solver (positions and topology are untouched), so keep its validation."""
    from . import validation_state
    validation_state.keep_record_through_update(obj)
    write()


def ensure_cell_attribute(obj) -> None:
    mesh = obj.data
    count = len(mesh.polygons)
    expected = np.arange(1, count + 1, dtype=np.float32)
    attribute = mesh.attributes.get(ATTRIBUTE)
    if attribute is not None and attribute.domain == "FACE" and attribute.data_type == "FLOAT" \
            and len(attribute.data) == count:
        current = np.empty(count, np.float32)
        attribute.data.foreach_get("value", current)
        if np.array_equal(current, expected):
            return
    if mesh.users > 1 or mesh.library or obj.library:
        raise ShaderError(f"{obj.name}: make the mesh local and single-user before baking wetness")

    def write():
        nonlocal attribute
        if attribute is not None:
            mesh.attributes.remove(mesh.attributes[ATTRIBUTE])
        created = mesh.attributes.new(ATTRIBUTE, "FLOAT", "FACE")
        created.data.foreach_set("value", expected)
        mesh.update()

    _restore_validation_after(obj, write)


def sequence_image(obj, paths, first, last, normal=False):
    path = str(paths.normal_image(first) if normal else paths.frame_image(first))
    name = normal_image_name(obj) if normal else image_name(obj)
    image = bpy.data.images.get(name)
    if image is None or not image.get(OWNED):
        image = bpy.data.images.load(path, check_existing=False)
        image.name = name
        image[OWNED] = 1
    image.filepath = path
    image.source = "SEQUENCE"
    image.colorspace_settings.name = "Non-Color"
    image.alpha_mode = "NONE"
    image.reload()
    return image


def _materials(obj):
    seen = []
    for slot in obj.material_slots:
        material = slot.material
        if material is not None and material not in seen:
            seen.append(material)
    return seen


def _other_tethys_users(material, obj):
    for other in bpy.data.objects:
        if other is obj or other.type != "MESH":
            continue
        settings = getattr(other, "cloth_next", None)
        if settings is None or not getattr(settings.tethys, "cache_stem", ""):
            continue
        if any(slot.material is material for slot in other.material_slots):
            return other
    return None


def _capture(socket):
    if socket.is_linked:
        link = socket.links[0]
        return {"link": [link.from_node.name, link.from_socket.identifier]}
    value = socket.default_value
    try:
        value = list(value)
    except TypeError:
        value = float(value)
    return {"default": value}


def _restore(tree, socket, captured):
    for link in list(socket.links):
        tree.links.remove(link)
    if "link" in captured:
        node = tree.nodes.get(captured["link"][0])
        if node is not None:
            source = next((s for s in node.outputs if s.identifier == captured["link"][1]), None)
            if source is not None:
                tree.links.new(source, socket)
    elif "default" in captured:
        socket.default_value = captured["default"]


def _feed(tree, captured, target):
    if "link" in captured:
        node = tree.nodes.get(captured["link"][0])
        source = next((s for s in node.outputs if s.identifier == captured["link"][1]), None) if node else None
        if source is not None:
            tree.links.new(source, target)
            return
    if "default" in captured:
        target.default_value = captured["default"]


def _uv_name(obj) -> str:
    layers = obj.data.uv_layers
    layer = next((l for l in layers if l.active_render), None) or layers.active
    return layer.name if layer is not None else ""


def _insert(material, obj, image, size, first, last, uv_layout=False, normal_image=None):
    tree = material.node_tree
    settings = obj.cloth_next.tethys
    for principled in [n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled" and not n.get(OWNED)]:
        existing = next((n for n in tree.nodes if n.get(OWNED) and n.get("cnx_tethys_target") == principled.name
                         and n.bl_idname == "ShaderNodeGroup"), None)
        if existing is not None:
            texture = next((n for n in tree.nodes if n.get(OWNED) and n.bl_idname == "ShaderNodeTexImage"
                            and not n.get("cnx_tethys_normal")
                            and n.get("cnx_tethys_target") == principled.name), None)
            cell = next((n for n in tree.nodes if n.get(OWNED) and n.get("cnx_tethys_target") == principled.name
                         and n.bl_idname == "ShaderNodeGroup" and n.node_tree.name == CELL_GROUP), None)
            if texture is not None:
                _configure_texture(texture, image, first, last)
                if uv_layout:
                    texture.interpolation = "Linear"
            normals = next((n for n in tree.nodes if n.get(OWNED) and n.get("cnx_tethys_normal")
                            and n.get("cnx_tethys_target") == principled.name), None)
            if normals is not None and normal_image is not None:
                _configure_texture(normals, normal_image, first, last)
                normals.interpolation = "Linear"
            if cell is not None:
                cell.inputs["Width"].default_value, cell.inputs["Height"].default_value = size
            continue
        base, rough = principled.inputs["Base Color"], principled.inputs["Roughness"]
        coat = principled.inputs.get("Coat Weight")
        normal = principled.inputs["Normal"]
        record = {"base": _capture(base), "rough": _capture(rough), "normal": _capture(normal)}
        coat_free = coat is not None and not coat.is_linked and float(coat.default_value) == 0.0
        if coat_free:
            record["coat"] = _capture(coat)
        x, y = principled.location
        nodes = []

        def add(kind, offset):
            node = tree.nodes.new(kind)
            node.location = (x + offset[0], y + offset[1])
            node[OWNED] = 1
            node["cnx_tethys_target"] = principled.name
            nodes.append(node)
            return node

        attribute = add("ShaderNodeAttribute", (-1200, -500))
        attribute.attribute_type = "GEOMETRY"
        attribute.attribute_name = ATTRIBUTE
        cell = add("ShaderNodeGroup", (-1000, -500))
        cell.node_tree = cell_group()
        cell.inputs["Width"].default_value, cell.inputs["Height"].default_value = size
        texture = add("ShaderNodeTexImage", (-800, -500))
        _configure_texture(texture, image, first, last)
        if uv_layout:
            # Streaks and beads live in the UV wetness texture; the cell
            # attribute only tells TETHYS objects apart from others.
            texture.interpolation = "Linear"
            uv = add("ShaderNodeUVMap", (-1000, -700))
            uv.uv_map = _uv_name(obj)
        split = add("ShaderNodeSeparateColor", (-520, -500))
        group = add("ShaderNodeGroup", (-300, -250))
        group.node_tree = wetness_group()
        group.label = "TETHYS Wetness"
        group[RECORD] = json.dumps(record)
        tree.links.new(attribute.outputs["Fac"], cell.inputs["Cell"])
        tree.links.new(uv.outputs["UV"] if uv_layout else cell.outputs["Vector"], texture.inputs["Vector"])
        tree.links.new(texture.outputs["Color"], split.inputs[0])
        tree.links.new(split.outputs[0], group.inputs["Surface Water"])
        tree.links.new(split.outputs[1], group.inputs["Absorbed Water"])
        if uv_layout:
            # Drops and rivulets catch the light through the engine's water
            # normal. Where water covers the fabric its smooth surface replaces
            # the fabric's own normal (kept everywhere else; Clear restores it).
            normals = add("ShaderNodeTexImage", (-800, -800))
            normals["cnx_tethys_normal"] = 1
            _configure_texture(normals, normal_image, first, last)
            normals.interpolation = "Linear"
            tree.links.new(uv.outputs["UV"], normals.inputs["Vector"])
            tree.links.new(normals.outputs["Color"], group.inputs["Normal Map"])
            water = add("ShaderNodeNormalMap", (-50, -650))
            water.space = "TANGENT"
            water.uv_map = _uv_name(obj)
            water.inputs["Strength"].default_value = settings.water_relief
            tree.links.new(group.outputs["Water Normal"], water.inputs["Color"])
            fabric = add("ShaderNodeMix", (150, -550))
            fabric.data_type = "VECTOR"
            fabric.clamp_factor = True
            fabric_a = next(s for s in fabric.inputs if s.name == "A" and s.type == "VECTOR")
            fabric_b = next(s for s in fabric.inputs if s.name == "B" and s.type == "VECTOR")
            if "link" in record["normal"]:
                _feed(tree, record["normal"], fabric_a)
            else:
                geometry = add("ShaderNodeNewGeometry", (-50, -450))
                tree.links.new(geometry.outputs["Normal"], fabric_a)
            tree.links.new(group.outputs["Film"], fabric.inputs["Factor"])
            tree.links.new(water.outputs["Normal"], fabric_b)
            unit = add("ShaderNodeVectorMath", (350, -550))
            unit.operation = "NORMALIZE"
            tree.links.new(next(s for s in fabric.outputs if s.type == "VECTOR"), unit.inputs[0])
            tree.links.new(unit.outputs["Vector"], normal)
        tree.links.new(cell.outputs["Valid"], group.inputs["Valid"])
        _feed(tree, record["base"], group.inputs["Base Color"])
        _feed(tree, record["rough"], group.inputs["Roughness"])
        tree.links.new(group.outputs["Base Color"], base)
        tree.links.new(group.outputs["Roughness"], rough)
        if coat_free:
            group.inputs["Coat Weight"].default_value = 0.0
            tree.links.new(group.outputs["Coat Weight"], coat)
        _apply_appearance(group, settings)


def _configure_texture(texture, image, first, last):
    texture.image = image
    texture.interpolation = "Closest"
    texture.extension = "EXTEND"
    user = texture.image_user
    user.frame_start = first
    user.frame_offset = first - 1
    user.frame_duration = last - first + 1
    user.use_auto_refresh = True
    user.use_cyclic = False


def _apply_appearance(group, settings):
    group.inputs["Darkening"].default_value = settings.darkening
    group.inputs["Wet Roughness"].default_value = settings.wet_roughness
    group.inputs["Film Response"].default_value = settings.film_response
    group.inputs["Strength"].default_value = settings.appearance_strength
    group.inputs["Moisture Response"].default_value = settings.moisture_response


def attach(obj, paths, size, first, last, uv_layout=False) -> list[str]:
    """Connect every material of `obj`; returns notes for materials left unchanged."""
    notes = []
    materials = _materials(obj)
    for material in materials:
        other = _other_tethys_users(material, obj)
        if other is not None:
            raise ShaderError(f"Material {material.name!r} is also used by TETHYS object {other.name!r}. "
                              "Make it single-user first")
    ensure_cell_attribute(obj)
    image = sequence_image(obj, paths, first, last)
    normal_image = sequence_image(obj, paths, first, last, normal=True) if uv_layout else None
    for material in materials:
        # Material.use_nodes is deprecated (Blender 6 removes it); the tree decides.
        if material.node_tree is None or not getattr(material, "use_nodes", True):
            notes.append(f"{material.name}: no shader nodes")
            continue
        if not any(n.bl_idname == "ShaderNodeBsdfPrincipled" for n in material.node_tree.nodes):
            notes.append(f"{material.name}: no Principled BSDF")
            continue
        _insert(material, obj, image, size, first, last, uv_layout, normal_image)
    if not materials:
        notes.append("no material slots")
    return notes


def update_appearance(obj) -> None:
    settings = obj.cloth_next.tethys
    for material in _materials(obj):
        tree = material.node_tree
        if tree is None:
            continue
        for node in tree.nodes:
            if node.get(OWNED) and node.bl_idname == "ShaderNodeGroup" and node.node_tree and \
                    node.node_tree.name == GROUP:
                _apply_appearance(node, settings)
            elif node.get(OWNED) and node.bl_idname == "ShaderNodeNormalMap":
                node.inputs["Strength"].default_value = settings.water_relief


def detach(obj, keep_attribute=False) -> None:
    """Restore every material exactly and remove all TETHYS nodes and the image."""
    for material in _materials(obj):
        tree = material.node_tree
        if tree is None:
            continue
        owned = [n for n in tree.nodes if n.get(OWNED)]
        for group in [n for n in owned if n.get(RECORD)]:
            principled = tree.nodes.get(group.get("cnx_tethys_target", ""))
            if principled is None:
                continue
            record = json.loads(group[RECORD])
            _restore(tree, principled.inputs["Base Color"], record["base"])
            _restore(tree, principled.inputs["Roughness"], record["rough"])
            if "normal" in record:
                _restore(tree, principled.inputs["Normal"], record["normal"])
            if "coat" in record and principled.inputs.get("Coat Weight") is not None:
                _restore(tree, principled.inputs["Coat Weight"], record["coat"])
        for node in owned:
            tree.nodes.remove(node)
    for name in (image_name(obj), normal_image_name(obj)):
        image = bpy.data.images.get(name)
        if image is not None and image.get(OWNED):
            bpy.data.images.remove(image)
    mesh = obj.data
    if keep_attribute:
        return
    if mesh.attributes.get(ATTRIBUTE) is not None and mesh.users == 1 and not mesh.library:
        _restore_validation_after(obj, lambda: mesh.attributes.remove(mesh.attributes[ATTRIBUTE]))
