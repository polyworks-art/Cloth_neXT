"""Neutral UI terminology must not change the persisted solver identity."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace

from cloth_next.simulation.backends import BackendId, PPF_BACKEND


def test_backend_label_is_separate_from_persisted_identity():
    assert BackendId.PPF.value == "PPF"
    assert PPF_BACKEND.identifier is BackendId.PPF
    assert PPF_BACKEND.display_name == "Simulation Solver"


def test_visible_labels_tooltips_and_operator_descriptions_are_neutral():
    root = Path(__file__).parents[1] / "cloth_next" / "blender"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        visible = []
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg in {"text", "description", "name", "title"}:
                visible.extend(n.value for n in ast.walk(node.value)
                               if isinstance(n, ast.Constant) and isinstance(n.value, str))
            if isinstance(node, ast.Assign) and any(
                isinstance(n, ast.Name) and n.id in {"bl_label", "bl_description"}
                for n in node.targets
            ):
                visible.extend(n.value for n in ast.walk(node.value)
                               if isinstance(n, ast.Constant) and isinstance(n.value, str))
            if isinstance(node, ast.ClassDef) and any(
                isinstance(base, ast.Attribute) and base.attr == "Operator" for base in node.bases
            ):
                visible.append(ast.get_docstring(node) or "")
        assert all(not re.search(r"\bPPF\b", text, re.I) for text in visible), path


def test_legacy_display_name_is_presented_without_mutating_it(blender_env, monkeypatch):
    from cloth_next.blender import solver_release_naming
    monkeypatch.setattr(solver_release_naming, "_entry_for_installation", lambda _: None)
    installation = SimpleNamespace(display_name="Custom PPF 0.13")
    assert solver_release_naming.release_name(installation) == "Custom Solver 0.13"
    assert installation.display_name == "Custom PPF 0.13"
