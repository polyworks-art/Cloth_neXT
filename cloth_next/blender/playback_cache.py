# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authoritative Cloth NeXt playback ownership and evaluation exclusion."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path

OWNERSHIP_MARKER="cloth_next_playback_v1"
OBJECT_OWNERSHIP_KEY="cloth_next_playback_owner"

def _property(value,key,default=None):
    try:return value.get(key,default)
    except (AttributeError,TypeError):return getattr(value,key,default)

def mark_owned_playback(obj,modifier,cache_path:str)->None:
    # Blender 5.2 modifiers do not necessarily support ID properties. The
    # Object is an ID datablock and therefore owns the authoritative marker;
    # the modifier marker remains a compatible best-effort hint.
    for target,key,value,required in (
            (modifier,"cloth_next_owner",OWNERSHIP_MARKER,False),
            (obj,OBJECT_OWNERSHIP_KEY,OWNERSHIP_MARKER,True),
            (obj,"cloth_next_cache_path",str(cache_path),True)):
        try:
            target[key]=value
            continue
        except (TypeError,AttributeError):
            pass
        try:
            setattr(target,key,value)
        except (TypeError,AttributeError):
            if required:
                raise

def has_cloth_next_playback_marker(obj,modifier)->bool:
    """Cheap, syscall-free ownership classification for read-only UI paths.

    Compares the ownership marker and inspects the recorded path as a plain
    string. It never touches the filesystem, so it is safe to call from a
    ``Panel.draw``. Use :func:`is_cloth_next_playback_modifier` — which
    additionally resolves both paths on disk — for anything that deletes or
    replaces a cache file.
    """
    if str(getattr(modifier,"type",""))!="MESH_CACHE":return False
    marker=(_property(modifier,"cloth_next_owner","")
            or _property(obj,OBJECT_OWNERSHIP_KEY,""))
    actual=str(getattr(modifier,"filepath","") or "")
    if marker!=OWNERSHIP_MARKER:
        settings=getattr(obj,"cloth_next",None)
        return (getattr(modifier,"name","")=="Cloth NeXt Test Cache"
                and bool(getattr(settings,"baked_settings_fingerprint",""))
                and Path(actual).name.startswith("cn_test_cloth_")
                and Path(actual).suffix.lower()==".pc2")
    recorded=str(_property(obj,"cloth_next_cache_path","") or "")
    return bool(recorded and actual)

def is_cloth_next_playback_modifier(obj,modifier)->bool:
    """Authoritative ownership check; resolves both paths on disk.

    The ``resolve()`` comparison is the safety property that stops Cloth NeXt
    from ever unlinking or overwriting a file it does not own, so every
    destructive path keeps using this. It performs filesystem syscalls and is
    therefore never called from a draw path.
    """
    if not has_cloth_next_playback_marker(obj,modifier):return False
    marker=(_property(modifier,"cloth_next_owner","")
            or _property(obj,OBJECT_OWNERSHIP_KEY,""))
    if marker!=OWNERSHIP_MARKER:return True  # legacy: fully classified above
    recorded=str(_property(obj,"cloth_next_cache_path","") or "")
    actual=str(getattr(modifier,"filepath","") or "")
    try:return Path(recorded).resolve()==Path(actual).resolve()
    except OSError:return recorded==actual

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
