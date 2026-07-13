# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authoritative Cloth NeXt playback ownership and evaluation exclusion."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path

OWNERSHIP_MARKER="cloth_next_playback_v1"

def _property(value,key,default=None):
    try:return value.get(key,default)
    except (AttributeError,TypeError):return getattr(value,key,default)

def mark_owned_playback(obj,modifier,cache_path:str)->None:
    # Blender 5.1 modifiers do not support ID properties.  The object is the
    # authoritative owner; writing it first also makes attachment atomic when
    # older Blender versions happen to support a modifier marker.
    try:
        obj["cloth_next_cache_path"] = str(cache_path)
    except (TypeError, AttributeError):
        setattr(obj, "cloth_next_cache_path", str(cache_path))
    try:
        modifier["cloth_next_owner"] = OWNERSHIP_MARKER
    except (TypeError, AttributeError):
        try:
            setattr(modifier, "cloth_next_owner", OWNERSHIP_MARKER)
        except (TypeError, AttributeError):
            pass

def is_cloth_next_playback_modifier(obj,modifier)->bool:
    if str(getattr(modifier,"type",""))!="MESH_CACHE":return False
    marker=_property(modifier,"cloth_next_owner","")
    recorded=str(_property(obj,"cloth_next_cache_path","") or "")
    actual=str(getattr(modifier,"filepath","") or "")
    paths_match = False
    if recorded and actual:
        try: paths_match = Path(recorded).resolve() == Path(actual).resolve()
        except OSError: paths_match = recorded == actual
    if marker!=OWNERSHIP_MARKER:
        settings=getattr(obj,"cloth_next",None)
        canonical=(getattr(modifier,"name","")=="Cloth NeXt Test Cache"
                   and Path(actual).name.startswith("cn_test_cloth_")
                   and Path(actual).suffix.lower()==".pc2")
        legacy=canonical and (paths_match or bool(
            getattr(settings,"baked_settings_fingerprint","")))
        if not legacy:return False
        return True
    if not recorded or not actual:return False
    return paths_match

@contextmanager
def without_owned_playback(obj,update=None):
    states=[]
    for modifier in getattr(obj,"modifiers",()):
        if not is_cloth_next_playback_modifier(obj,modifier):continue
        state=(modifier,getattr(modifier,"show_viewport",True),
               getattr(modifier,"show_render",True)); states.append(state)
        modifier.show_viewport=False; modifier.show_render=False
    if states and update:update()
    try:yield tuple(modifier for modifier,_,_ in states)
    finally:
        for modifier,viewport,render in states:
            modifier.show_viewport=viewport; modifier.show_render=render
        if states and update:update()
