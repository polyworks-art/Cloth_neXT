from pathlib import Path

import pytest

from cloth_next.platform_support import (
    LINUX_X64, WINDOWS_X64, managed_solver_root, platform_spec)
from cloth_next.updater.install_paths import _validate_executable_relative


def test_supported_platform_descriptions_are_exact():
    assert platform_spec(sys_platform="win32", machine="AMD64") == WINDOWS_X64
    assert platform_spec(sys_platform="linux", machine="x86_64") == LINUX_X64
    assert WINDOWS_X64.companion_filename == "cloth-next-bake.exe"
    assert LINUX_X64.companion_filename == "cloth-next-bake"


def test_linux_xdg_managed_root_does_not_use_real_home(tmp_path):
    assert managed_solver_root(
        LINUX_X64, environ={"XDG_DATA_HOME": str(tmp_path / "xdg")},
        home=tmp_path / "home") == (tmp_path / "xdg" / "ClothNeXt" / "solver").resolve()
    assert managed_solver_root(
        LINUX_X64, environ={}, home=tmp_path / "home") == (
            tmp_path / "home" / ".local" / "share" / "ClothNeXt" / "solver").resolve()


@pytest.mark.parametrize("spec,valid,foreign", [
    (WINDOWS_X64, "target/release/ppf-cts-server.exe", "ppf-cts-server"),
    (LINUX_X64, "target/release/ppf-cts-server", "ppf-cts-server.exe"),
])
def test_managed_executable_identity_is_platform_strict(spec, valid, foreign):
    assert _validate_executable_relative(valid, platform=spec) == valid
    for invalid in (foreign, "../" + valid, "/" + valid, "C:/" + valid):
        with pytest.raises(ValueError):
            _validate_executable_relative(invalid, platform=spec)
