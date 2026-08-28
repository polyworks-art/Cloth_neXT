# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authoritative Cloth NeXt playback ownership and evaluation exclusion."""
from __future__ import annotations
from contextlib import contextmanager
import json
from pathlib import Path
from collections import deque

OWNERSHIP_MARKER="cloth_next_playback_v1"
OBJECT_OWNERSHIP_KEY="cloth_next_playback_owner"
INPUT_DEFORMER_STATE_KEY="cloth_next_playback_input_deformers_v1"
INPUT_DEFORMER_TYPES=frozenset({"ARMATURE", "CORRECTIVE_SMOOTH"})
_PENDING_CLEANUP_LIMIT = 128
_pending_cleanup = deque(maxlen=_PENDING_CLEANUP_LIMIT)


def record_pending_cleanup(path) -> None:
    """Remember obsolete owned cache garbage without changing playback state."""
    value = Path(path).resolve()
    if value not in _pending_cleanup:
        _pending_cleanup.append(value)


def pending_cleanup_paths() -> tuple[Path, ...]:
    """Return the bounded process-local cleanup backlog (oldest first)."""
    return tuple(_pending_cleanup)


def forget_pending_cleanup(path) -> None:
    value = Path(path).resolve()
    try:
        _pending_cleanup.remove(value)
    except ValueError:
        pass

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
    modifier_marker=_property(modifier,"cloth_next_owner","")
    marker=(modifier_marker or _property(obj,OBJECT_OWNERSHIP_KEY,""))
    actual=str(getattr(modifier,"filepath","") or "")
    if marker!=OWNERSHIP_MARKER:
        settings=getattr(obj,"cloth_next",None)
        return (getattr(modifier,"name","")=="Cloth NeXt Test Cache"
                and bool(getattr(settings,"baked_settings_fingerprint",""))
                and Path(actual).name.startswith("cn_test_cloth_")
                and Path(actual).suffix.lower()==".pc2")
    recorded=str(_property(obj,"cloth_next_cache_path","") or "")
    if modifier_marker==OWNERSHIP_MARKER:return bool(actual)
    # The object marker is shared by every modifier on the object.  Its path
    # is what identifies the one owned Mesh Cache without filesystem I/O;
    # accepting any non-empty path lets retargeting hijack an artist cache
    # that appears earlier in the modifier stack.
    return bool(recorded and actual and recorded==actual)

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


def _stored_input_deformer_states(obj):
    raw = _property(obj, INPUT_DEFORMER_STATE_KEY, "")
    if not raw:
        return ()
    try:
        values = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return tuple(value for value in values if isinstance(value, dict))


def _find_stored_modifier(obj, state):
    modifiers = tuple(getattr(obj, "modifiers", ()))
    name = str(state.get("name", ""))
    modifier_type = str(state.get("type", ""))
    for modifier in modifiers:
        if (str(getattr(modifier, "name", "")) == name
                and str(getattr(modifier, "type", "")) == modifier_type):
            return modifier
    index = state.get("index")
    if isinstance(index, int) and 0 <= index < len(modifiers):
        candidate = modifiers[index]
        if str(getattr(candidate, "type", "")) == modifier_type:
            return candidate
    return None


def restore_playback_input_deformers(obj, *, clear=False) -> bool:
    """Restore rig deformation that an owned absolute PC2 temporarily mutes."""
    states = _stored_input_deformer_states(obj)
    changed = False
    for state in states:
        modifier = _find_stored_modifier(obj, state)
        if modifier is None:
            continue
        viewport = bool(state.get("show_viewport", True))
        render = bool(state.get("show_render", True))
        changed |= (bool(getattr(modifier, "show_viewport", True)) != viewport
                    or bool(getattr(modifier, "show_render", True)) != render)
        modifier.show_viewport = viewport
        modifier.show_render = render
    if clear:
        try:
            del obj[INPUT_DEFORMER_STATE_KEY]
        except (AttributeError, KeyError, TypeError):
            try:
                delattr(obj, INPUT_DEFORMER_STATE_KEY)
            except AttributeError:
                pass
    return changed


def mute_playback_input_deformers(obj, playback_modifier=None) -> bool:
    """Mute deformations already baked into an absolute Mesh Cache.

    The snapshot lives on the Object ID datablock so Clear can restore the
    artist's exact modifier flags after saving and reopening a blend file.
    """
    states = _stored_input_deformer_states(obj)
    modifiers = tuple(getattr(obj, "modifiers", ()))
    if not states:
        try:
            playback_index = modifiers.index(playback_modifier)
        except (ValueError, AttributeError):
            playback_index = len(modifiers)
        states = tuple({
            "name": str(getattr(modifier, "name", "")),
            "type": str(getattr(modifier, "type", "")),
            "index": index,
            "show_viewport": bool(getattr(modifier, "show_viewport", True)),
            "show_render": bool(getattr(modifier, "show_render", True)),
        } for index, modifier in enumerate(modifiers[:playback_index])
            if str(getattr(modifier, "type", "")) in INPUT_DEFORMER_TYPES)
        if states:
            encoded = json.dumps(states, sort_keys=True, separators=(",", ":"))
            try:
                obj[INPUT_DEFORMER_STATE_KEY] = encoded
            except (AttributeError, TypeError):
                setattr(obj, INPUT_DEFORMER_STATE_KEY, encoded)
    changed = False
    for state in states:
        modifier = _find_stored_modifier(obj, state)
        if modifier is None:
            continue
        changed |= (bool(getattr(modifier, "show_viewport", True))
                    or bool(getattr(modifier, "show_render", True)))
        modifier.show_viewport = False
        modifier.show_render = False
    return changed

@contextmanager
def without_owned_playback(obj,update=None):
    states=[]
    for modifier in getattr(obj,"modifiers",()):
        if not is_cloth_next_playback_modifier(obj,modifier):continue
        state=(modifier,getattr(modifier,"show_viewport",True),
               getattr(modifier,"show_render",True)); states.append(state)
        modifier.show_viewport=False; modifier.show_render=False
    input_restored = restore_playback_input_deformers(obj) if states else False
    if (states or input_restored) and update:update()
    try:yield tuple(modifier for modifier,_,_ in states)
    finally:
        input_muted = mute_playback_input_deformers(
            obj, states[0][0] if states else None) if states else False
        for modifier,viewport,render in states:
            modifier.show_viewport=viewport; modifier.show_render=render
        if (states or input_muted) and update:update()
