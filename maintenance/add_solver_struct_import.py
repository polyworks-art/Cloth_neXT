from pathlib import Path

path = Path("cloth_next/blender/solver_test.py")
text = path.read_text(encoding="utf-8")
old = "import shutil\nimport threading\n"
new = "import shutil\nimport struct\nimport threading\n"
if text.count(old) != 1:
    raise RuntimeError("solver_test import block changed")
path.write_text(text.replace(old, new), encoding="utf-8")
Path("maintenance/add_solver_struct_import.py").unlink()
