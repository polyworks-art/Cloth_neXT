# SPDX-License-Identifier: GPL-3.0-or-later

"""Process-wide Bake ownership survives duplicate add-on module generations."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_copy(name: str):
    path = (Path(__file__).parents[1] / "cloth_next" / "blender" /
            "modal_lock.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_duplicate_module_generation_cannot_steal_reserved_bake(blender_env):
    first = _load_copy("clothnext_modal_lock_first")
    second = _load_copy("clothnext_modal_lock_second")

    assert first.reserve("newton-job")
    assert not second.reserve("stale-ppf-job")
    assert not second.acquire(
        "stale-ppf-job", companion_ready_job_id="stale-ppf-job")
    assert first.acquire(
        "newton-job", companion_ready_job_id="newton-job")

    # A rejected stale generation must not disturb the real owner.
    second.release("stale-ppf-job")
    assert first.active("newton-job")
    first.release("newton-job")
    assert second.reserve("next-job")
    second.release("next-job")
