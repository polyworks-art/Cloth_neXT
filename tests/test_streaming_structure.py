from pathlib import Path


def test_production_worker_cannot_regress_to_animation_materialization():
    source = (Path(__file__).parents[1] / "cloth_next" / "blender" / "solver_test.py").read_text(encoding="utf-8")
    worker = source.split("def _worker_main", 1)[1].split("def _attach_playback", 1)[0]
    for forbidden in ("list[SolverFrame]", "frames.append", "build_playback_frames(",
                      "write_playback_cache("):
        assert forbidden not in worker


def test_production_writer_has_no_vertex_pack_or_animation_body():
    source = (Path(__file__).parents[1] / "cloth_next" / "bake" / "pc2.py").read_text(encoding="utf-8")
    writer = source.split("class StreamingPc2Writer", 1)[1].split("def write_pc2", 1)[0]
    assert 'struct.pack("<3f"' not in writer
    assert "body = bytearray" not in writer
