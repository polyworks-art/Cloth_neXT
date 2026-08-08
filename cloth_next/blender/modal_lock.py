# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-wide Bake reservation and modal token.

The state lives on :mod:`builtins`, which is shared by every generation and
extension namespace in one Blender process.  A module-local token is too late
and too narrow: an extension reload can otherwise start a second controller
while the first generation owns the common Bake window.
"""

import builtins

_STATE_KEY = "_clothnext_process_bake_lock_v1"


def _state():
    value = getattr(builtins, _STATE_KEY, None)
    if not isinstance(value, dict):
        value = {"reserved": "", "modal": ""}
        setattr(builtins, _STATE_KEY, value)
    return value


def reserve(job_id: str) -> bool:
    """Reserve Bake startup before any Companion process is launched."""
    if not job_id:
        return False
    state = _state()
    owner = str(state.get("reserved", "") or "")
    if owner and owner != job_id:
        return False
    state["reserved"] = job_id
    return True


def acquire(job_id: str, *, companion_ready_job_id: str) -> bool:
    if not job_id or companion_ready_job_id != job_id:
        return False
    state = _state()
    reserved = str(state.get("reserved", "") or "")
    modal = str(state.get("modal", "") or "")
    if reserved and reserved != job_id:
        return False
    if modal and modal != job_id:
        return False
    state["reserved"] = job_id
    state["modal"] = job_id
    return True


def release(job_id: str | None = None) -> None:
    state = _state()
    if job_id is None:
        state["reserved"] = ""
        state["modal"] = ""
        return
    if state.get("reserved") == job_id:
        state["reserved"] = ""
    if state.get("modal") == job_id:
        state["modal"] = ""


def active(job_id: str | None = None) -> bool:
    owner = str(_state().get("modal", "") or "")
    return bool(owner) and (job_id is None or job_id == owner)
