from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_artist_recovery_gate_uses_only_public_production_entrypoints():
    source = (ROOT / "tools" / "blender_recovery_ui_gate.py").read_text(
        encoding="utf-8")

    assert 'bpy.ops.clothnext.bake("EXEC_DEFAULT")' in source
    assert 'bpy.ops.clothnext.recovery_resume_latest("EXEC_DEFAULT")' in source
    assert "bpy.ops.wm.open_mainfile" in source
    assert "resume_requested =" not in source
    assert "_worker_main" not in source
    assert "_plan(" not in source
