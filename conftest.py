"""Repository-wide pytest command-line contracts."""
import os
from pathlib import Path

# The historical unit suite is the Windows regression gate even when collected
# by an Ubuntu runner. Linux-specific tests pass an explicit PlatformSpec; real
# Linux build/smoke jobs intentionally run outside this pytest override.
os.environ.setdefault("CLOTH_NEXT_PLATFORM_OVERRIDE", "windows-x64")

import pytest


def pytest_addoption(parser):
    parser.addoption("--extension-zip", type=Path,
                     help="fully built release candidate ZIP")


@pytest.fixture
def extension_zip(request):
    value = request.config.getoption("--extension-zip")
    if value is None:
        pytest.fail("built-artifact tests require --extension-zip PATH")
    path = Path(value).resolve()
    if not path.is_file():
        pytest.fail(f"release candidate ZIP does not exist: {path}")
    return path
