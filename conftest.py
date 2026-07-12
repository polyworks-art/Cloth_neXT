"""Repository-wide pytest command-line contracts."""
from pathlib import Path

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
