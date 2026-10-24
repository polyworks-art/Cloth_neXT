"""Prepared FLIP surfaces are reused while the FLIP cache is unchanged."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from cloth_next.gaia import hydrodynamic_session as module

FAKE = r'''
import argparse, json, pathlib
p = argparse.ArgumentParser()
p.add_argument("--output"); p.add_argument("--cache"); p.add_argument("--first")
p.add_argument("--frames", type=int); p.add_argument("--source-last"); p.add_argument("--fps")
p.add_argument("--surface-scale"); p.add_argument("--bounds", nargs="*")
a = p.parse_args()
out = pathlib.Path(a.output); out.mkdir(parents=True)
(out / "hydrodynamics.json").write_text(json.dumps({"frames": a.frames,
    "roles": ["velocity_m_s_support", "signed_distance_m_x_identity"]}))
(out / "bricks.bin").write_bytes(b"x" * 16)
log = pathlib.Path(a.cache).parent / "runs.txt"
log.write_text(log.read_text() + "run\n" if log.exists() else "run\n")
'''


def setup(tmp_path, monkeypatch):
    flip = tmp_path / "flip"; (flip / "bakefiles").mkdir(parents=True)
    (flip / "bakefiles" / "mesh_000001.bobj").write_bytes(b"mesh")
    script = tmp_path / "fake_preparer.py"; script.write_text(FAKE)
    monkeypatch.setattr(module, "experimental_configuration",
                        lambda _exe: {"python": sys.executable, "preparer": str(script)})
    monkeypatch.setenv(module.CACHE_ENV, str(tmp_path / "hydro-cache"))
    descriptor = {"path": str(flip), "first": 1, "last": 3, "fps": 24.0,
                  "bounds": [0, 0, 0, 1, 1, 1], "model": {"m": 1}}
    return flip, descriptor


def runs(tmp_path):
    log = tmp_path / "runs.txt"
    return len(log.read_text().splitlines()) if log.exists() else 0


def prepare(descriptor, tmp_path, name):
    work = tmp_path / name; work.mkdir()
    return module.prepare_session(descriptor, "solver.exe", work,
                                  check_cancel=lambda: None, progress=lambda c, t: None)


def test_unchanged_flip_cache_reuses_prepared_surfaces(tmp_path, monkeypatch):
    flip, descriptor = setup(tmp_path, monkeypatch)
    first = prepare(descriptor, tmp_path, "w1")
    second = prepare(descriptor, tmp_path, "w2")
    assert runs(tmp_path) == 1
    assert first["PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR"] == second["PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR"]
    assert module.is_shared_volume(second["PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR"])
    # Each run keeps its own model file in its work folder.
    assert Path(second["PPF_EXPERIMENTAL_HYDRO_TEST_JSON"]).parent.parent == tmp_path / "w2"
    assert json.loads(Path(second["PPF_EXPERIMENTAL_HYDRO_TEST_JSON"]).read_text()) == {"m": 1}


def test_rebaked_flip_or_new_range_prepares_again_and_old_entries_are_pruned(tmp_path, monkeypatch):
    flip, descriptor = setup(tmp_path, monkeypatch)
    prepare(descriptor, tmp_path, "w1")
    (flip / "bakefiles" / "mesh_000001.bobj").write_bytes(b"rebaked mesh")
    prepare(descriptor, tmp_path, "w2")
    assert runs(tmp_path) == 2
    prepare({**descriptor, "last": 4}, tmp_path, "w3")
    assert runs(tmp_path) == 3
    entries = [p for p in (tmp_path / "hydro-cache").iterdir() if p.is_dir()]
    assert len(entries) == module.KEEP_ENTRIES


def test_failed_preparation_leaves_no_cache_entry(tmp_path, monkeypatch):
    flip, descriptor = setup(tmp_path, monkeypatch)
    (tmp_path / "fake_preparer.py").write_text("raise SystemExit(3)")
    import pytest
    from cloth_next.core.errors import ClothNextError
    with pytest.raises(ClothNextError):
        prepare(descriptor, tmp_path, "w1")
    assert not any((tmp_path / "hydro-cache").iterdir())
