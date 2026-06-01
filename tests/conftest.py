import pytest
import sys
from pathlib import Path

# Add project root to sys.path so that imports work when tests are run from anywhere
_project_root = Path(__file__).resolve().parent
while not (_project_root / "src").exists() and _project_root != _project_root.parent:
    _project_root = _project_root.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def pytest_ignore_collect(collection_path, config):
    """Skip e2e tests - they require a running server."""
    if "e2e" in str(collection_path):
        return True
    return False
