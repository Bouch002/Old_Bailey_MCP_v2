import sys
from pathlib import Path

import pytest

# Allow tests to import server.py from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))


def pytest_addoption(parser):
    parser.addoption("--run-slow", action="store_true", default=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (real API calls)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-slow"):
        skip = pytest.mark.skip(reason="Pass --run-slow to run API tests")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)
