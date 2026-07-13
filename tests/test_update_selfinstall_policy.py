# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Structural self-update safety policy (the updater crash hotfix).

Cloth NeXt must never install or replace its own currently running
extension package from inside Cloth NeXt code:
``bpy.ops.extensions.package_install(pkg_id="cloth_next")`` makes Blender
disable/replace/re-enable the extension whose code is still on the Python
stack — a native module-lifetime hazard that can crash Blender and cannot
be made safe with try/except or a deferred ``bpy.app.timers`` call. These
tests fail the suite if any such path returns to production code.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = REPO_ROOT / "cloth_next"
UPDATE_OPERATORS = PRODUCTION / "blender" / "addon_update_operators.py"


def _production_sources():
    for path in sorted(PRODUCTION.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path, path.read_text(encoding="utf-8")


def _attribute_names(node: ast.AST):
    """All attribute names used anywhere in a call's function expression."""
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            yield child.attr
        elif isinstance(child, ast.Name):
            yield child.id


def test_no_production_code_invokes_package_install():
    """AST-level: no production call whose target mentions package_install
    (documentation of the hazard is allowed; invoking it is not)."""
    for path, source in _production_sources():
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                names = set(_attribute_names(node.func))
                assert "package_install" not in names, (
                    f"{path}:{node.lineno} calls package_install — Cloth "
                    "NeXt must never install its own package; use the "
                    "repo_sync + native-update-view handoff instead")


def test_update_operators_never_touch_files_or_archives():
    """The update module may talk to Blender operators and read the channel
    index; it must never replace the active extension directory or extract
    an update ZIP itself."""
    source = UPDATE_OPERATORS.read_text(encoding="utf-8")
    for forbidden in ("zipfile", "extractall", "shutil", "rmtree",
                      "os.replace", "os.rename", "unlink", "write_bytes",
                      "urlretrieve", "TemporaryDirectory"):
        assert forbidden not in source, forbidden
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"zipfile", "shutil", "os", "subprocess", "tempfile",
                           "urllib"}, imported


def test_handoff_operator_schedules_no_timer_and_no_reload():
    """No timer-deferred self-install and no self-unregister/reload: the
    extension stays enabled and loaded, so deferring installation through
    ``bpy.app.timers`` would not fix the module-lifetime hazard."""
    source = UPDATE_OPERATORS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    handoff = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.ClassDef)
                   and node.name == "CLOTHNEXT_OT_addon_update_through_blender")
    handoff_source = ast.get_source_segment(source, handoff)
    for forbidden in ("timers", "package_install", "unregister",
                      "importlib", "reload", "addon_disable", "addon_enable",
                      "subprocess"):
        assert forbidden not in handoff_source, forbidden


def test_stale_installing_states_stay_removed():
    from cloth_next.updater.addon_updates import AddonUpdateState
    members = set(AddonUpdateState.__members__)
    # Opening the update view proves nothing about an installation, so no
    # state may claim one.
    assert "INSTALLING" not in members
    assert "RESTART_REQUIRED" not in members
    assert "READY_IN_BLENDER" in members
