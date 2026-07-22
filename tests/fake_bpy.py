# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A lightweight fake ``bpy`` for pytest — just enough Blender semantics.

Models the pieces Phase 2.8 relies on: class (un)registration with duplicate
detection, deferred property annotations (including PEP-563 stringized
annotations), a PointerProperty descriptor that materializes per-object
PropertyGroup instances with defaults, and ``PHYSICS_PT_add`` append/remove
draw-callback bookkeeping that mirrors Blender's ``_draw_funcs`` list.
"""

from __future__ import annotations

import sys
import types


def _is_property_group_type(target):
    return (isinstance(target, type) and
            any(base.__name__ == "PropertyGroup" for base in target.mro()))


class _PropDef:
    """Stand-in for a deferred bpy property definition."""

    def __init__(self, kind, **keywords):
        self.kind = kind
        self.keywords = keywords

    def default_value(self, id_data=None):
        if self.kind == "ENUM":
            items = self.keywords.get("items", ())
            if callable(items):
                items = items(None, None)
            default = self.keywords.get("default")
            if isinstance(default, int):
                return next((item[0] for item in items
                             if len(item) > 4 and item[4] == default), "")
            return default if default is not None else (items[0][0] if items else "")
        if self.kind == "POINTER":
            target = self.keywords["type"]
            if _is_property_group_type(target):
                return _instantiate_group(target, id_data)
            return None
        return self.keywords.get("default")

    def _name_on(self, owner):
        for klass in type.mro(owner):
            for name, value in vars(klass).items():
                if value is self:
                    return name
        raise AttributeError("property definition is not attached")

    def __get__(self, obj, owner):
        if obj is None:
            return self
        if self.kind != "POINTER":
            return self.default_value()
        name = self._name_on(owner)
        # The ID that owns the group — Blender exposes this as `id_data`, and
        # the add-on's update callbacks navigate through it.
        target = self.keywords["type"]
        if not _is_property_group_type(target):
            return obj.__dict__.get(name)
        instance = _instantiate_group(target, id_data=obj)
        obj.__dict__[name] = instance
        return instance


_PROP_CACHE: dict = {}


def _resolved_props(cls):
    """Resolve (possibly stringized) property annotations to _PropDef objects."""
    cached = _PROP_CACHE.get(cls)
    if cached is not None:
        return cached
    module = sys.modules.get(cls.__module__)
    module_globals = vars(module) if module else {}
    resolved = {}
    for name, annotation in getattr(cls, "__annotations__", {}).items():
        value = annotation
        if isinstance(value, str):
            value = eval(value, dict(module_globals))  # noqa: S307 — test fake
        if isinstance(value, _PropDef):
            resolved[name] = value
    _PROP_CACHE[cls] = resolved
    return resolved


def _instantiate_group(group_cls, id_data=None):
    """Build a PropertyGroup with its defaults, without firing update callbacks.

    Blender does not run a property's ``update`` callback while initializing
    defaults, so neither does this.
    """
    instance = group_cls()
    object.__setattr__(instance, "_id_data", id_data)
    for name, prop in _resolved_props(group_cls).items():
        target = prop.keywords.get("type")
        value = (_instantiate_group(target, id_data)
                 if prop.kind == "POINTER" and _is_property_group_type(target)
                 else prop.default_value())
        object.__setattr__(instance, name, value)
    return instance


class _Modifiers(list):
    def new(self, name, type):  # noqa: A002 — mirrors Blender signature
        modifier = types.SimpleNamespace(name=name, type=type)
        self.append(modifier)
        return modifier

    def move(self, from_index, to_index):
        self.insert(to_index, self.pop(from_index))


def make_module() -> types.ModuleType:
    """Build a fresh fake ``bpy`` module with isolated state."""
    bpy = types.ModuleType("bpy")

    class PropertyGroup:
        """Fires ``update=`` callbacks on assignment, exactly like Blender.

        Without this, a test that asserts "changing a material value marks the
        object dirty" would pass for the wrong reason — the callback would
        simply never run.
        """

        @property
        def id_data(self):
            return getattr(self, "_id_data", None)

        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)
            if name.startswith("_"):
                return
            prop = _resolved_props(type(self)).get(name)
            if prop is None:
                return
            update = prop.keywords.get("update")
            if update is not None:
                update(self, bpy.context)

    class Operator:
        def __init__(self):
            self.reports = []
            self.layout = None

        def report(self, levels, message):
            self.reports.append((set(levels), message))

    class Panel:
        pass

    class Menu:
        pass

    class AddonPreferences:
        pass

    class Object:
        def __init__(self, name="Object", type="MESH"):  # noqa: A002
            self.name = name
            self.type = type
            self.modifiers = _Modifiers()

    def _physics_add_draw(self, context):
        pass

    _physics_add_draw._draw_funcs = []

    class PHYSICS_PT_add:
        bl_space_type = "PROPERTIES"
        bl_region_type = "WINDOW"
        bl_context = "physics"
        draw = _physics_add_draw

        @classmethod
        def append(cls, draw_func):
            cls.draw._draw_funcs.append(draw_func)

        @classmethod
        def remove(cls, draw_func):
            cls.draw._draw_funcs.remove(draw_func)

    class Scene:
        pass

    types_module = types.SimpleNamespace(
        PropertyGroup=PropertyGroup, Operator=Operator, Panel=Panel, Menu=Menu,
        AddonPreferences=AddonPreferences, Object=Object, Scene=Scene,
        PHYSICS_PT_add=PHYSICS_PT_add)

    props_module = types.SimpleNamespace(
        BoolProperty=lambda **kw: _PropDef("BOOL", **kw),
        EnumProperty=lambda **kw: _PropDef("ENUM", **kw),
        StringProperty=lambda **kw: _PropDef("STRING", **kw),
        IntProperty=lambda **kw: _PropDef("INT", **kw),
        FloatProperty=lambda **kw: _PropDef("FLOAT", **kw),
        PointerProperty=lambda **kw: _PropDef("POINTER", **kw))

    registry: list = []

    def register_class(cls):
        if cls in registry:
            raise ValueError(f"register_class(...): already registered {cls}")
        registry.append(cls)
        setattr(types_module, cls.__name__, cls)

    def unregister_class(cls):
        if cls not in registry:
            raise RuntimeError(f"unregister_class(...): not registered {cls}")
        registry.remove(cls)
        delattr(types_module, cls.__name__)

    utils_module = types.SimpleNamespace(register_class=register_class,
                                         unregister_class=unregister_class)

    ops_log: list = []

    class _FakeOp:
        """Records calls; tests may set .raises or .side_effect."""

        def __init__(self, fullname):
            self.fullname = fullname
            self.raises = None
            self.side_effect = None

        def __call__(self, **kwargs):
            if self.raises is not None:
                raise self.raises
            ops_log.append((self.fullname, kwargs))
            if self.side_effect is not None:
                self.side_effect(**kwargs)
            return {"FINISHED"}

    extensions_repos: list = []

    def _repo_add_side_effect(**kwargs):
        module = f"repo_{len(extensions_repos)}"
        extensions_repos.append(types.SimpleNamespace(
            name=kwargs.get("name", ""), module=module,
            remote_url=kwargs.get("remote_url", ""), enabled=True,
            use_remote_url=True, directory=f"/fake/extensions/{module}"))

    repo_add_op = _FakeOp("preferences.extension_repo_add")
    repo_add_op.side_effect = _repo_add_side_effect

    ops_module = types.SimpleNamespace(
        extensions=types.SimpleNamespace(
            repo_sync=_FakeOp("extensions.repo_sync"),
            repo_sync_all=_FakeOp("extensions.repo_sync_all"),
            package_install=_FakeOp("extensions.package_install"),
            package_upgrade_all=_FakeOp("extensions.package_upgrade_all"),
            userpref_show_for_update=_FakeOp("extensions.userpref_show_for_update")),
        preferences=types.SimpleNamespace(
            extension_repo_add=repo_add_op,
            addon_show=_FakeOp("preferences.addon_show")),
        screen=types.SimpleNamespace(userpref_show=_FakeOp("screen.userpref_show")),
        clothnext=types.SimpleNamespace())

    context_preferences = types.SimpleNamespace(
        extensions=types.SimpleNamespace(repos=extensions_repos, active_repo=0),
        addons={})

    timer_functions: list = []

    def timers_register(func, first_interval=0.0):
        timer_functions.append(func)

    def timers_unregister(func):
        timer_functions.remove(func)

    def timers_is_registered(func):
        return func in timer_functions

    # Blender's handler lists are plain Python lists; append/remove semantics
    # (and therefore duplicate-handler bugs) are reproduced exactly.
    handlers_module = types.SimpleNamespace(
        depsgraph_update_post=[], load_post=[], undo_post=[], redo_post=[])

    app_module = types.SimpleNamespace(
        online_access=True,
        tempdir="/fake/session-temp",
        handlers=handlers_module,
        timers=types.SimpleNamespace(register=timers_register,
                                     unregister=timers_unregister,
                                     is_registered=timers_is_registered,
                                     functions=timer_functions))

    class _NamedStore(dict):
        def new(self, name, *args):
            item = types.SimpleNamespace(name=name)
            self[name] = item
            return item

        def remove(self, item, **_kw):
            self.pop(getattr(item, "name", None), None)

    data_module = types.SimpleNamespace(
        objects=_NamedStore(), collections=_NamedStore(),
        meshes=_NamedStore())
    path_module = types.SimpleNamespace(abspath=lambda p: p)

    bpy.types = types_module
    bpy.props = props_module
    bpy.utils = utils_module
    bpy.app = app_module
    bpy.ops = ops_module
    bpy.data = data_module
    bpy.path = path_module
    bpy.context = types.SimpleNamespace(window_manager=None,
                                        preferences=context_preferences)
    bpy.registry = registry
    bpy.ops_log = ops_log
    return bpy
