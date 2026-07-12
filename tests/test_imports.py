import ast
import importlib
import sys
from pathlib import Path


PURE_MODULES = (
    "cloth_next.core.errors", "cloth_next.core.state", "cloth_next.core.events",
    "cloth_next.core.logging", "cloth_next.ppf.contracts", "cloth_next.ppf.models",
)


def test_pure_modules_import_without_bpy():
    sys.modules.pop("bpy", None)
    for module in PURE_MODULES:
        importlib.import_module(module)
    assert "bpy" not in sys.modules


def test_dependency_direction_has_no_bpy_import_outside_blender_adapter():
    root = Path(__file__).parents[1] / "cloth_next"
    violations = []
    for path in root.rglob("*.py"):
        if "blender" in path.relative_to(root).parts or path == root / "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(alias.name == "bpy" for alias in node.names):
                violations.append(path)
            if isinstance(node, ast.ImportFrom) and node.module == "bpy":
                violations.append(path)
    assert violations == []


def test_backend_contract_contains_no_transport_implementation():
    source = (Path(__file__).parents[1] / "cloth_next/ppf/contracts.py").read_text(encoding="utf-8")
    for forbidden in ("socket", "TCMD", "cbor2", "subprocess", "http"):
        assert forbidden not in source

