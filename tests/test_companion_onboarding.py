# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace

import pytest

from companion import app
from companion.onboarding_window import InfoWindow, load_content
from cloth_next.onboarding import default_resource_root


@pytest.mark.parametrize("mode,version", [("welcome", None),
                                           ("whats-new", "2.3.5")])
def test_cli_dispatches_informational_modes_without_bake_transport(
        monkeypatch, mode, version):
    calls = []
    monkeypatch.setattr("companion.onboarding_window.run_info_window",
                        lambda selected, selected_version, content_root: calls.append(
                            (selected, selected_version, content_root)))
    arguments = ["--mode", mode, "--content-root", str(default_resource_root())]
    if version:
        arguments += ["--version", version]
    app.main(arguments)
    assert calls == [(mode, version, default_resource_root())]


def test_whats_new_cli_requires_version():
    with pytest.raises(SystemExit) as exc:
        app.main(["--mode", "whats-new", "--content-root",
                  str(default_resource_root())])
    assert exc.value.code == 2


def test_invalid_or_missing_resource_fails_without_starting_tk(tmp_path,
                                                               monkeypatch):
    with pytest.raises((OSError, ValueError)):
        load_content("welcome", content_root=tmp_path)
    with pytest.raises((OSError, ValueError)):
        load_content("whats-new", "not-a-version", tmp_path)


def test_info_window_close_terminates_its_ui_process_cleanly():
    destroyed = []
    window = InfoWindow.__new__(InfoWindow)
    window.root = SimpleNamespace(destroy=lambda: destroyed.append(True))
    window.close()
    assert destroyed == [True]
