# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from cloth_next.ppf.progress import read_progress


def test_missing_progress_file_is_safe(tmp_path):
    assert not read_progress(tmp_path / "later.log").ready


def test_starting_and_ready_markers(tmp_path):
    path = tmp_path / "progress.log"
    path.write_text("SERVER_STARTING\n", encoding="utf-8")
    assert read_progress(path).starting
    assert not read_progress(path).ready
    path.write_text("SERVER_STARTING\nSERVER_READY\n", encoding="utf-8")
    assert read_progress(path).ready

