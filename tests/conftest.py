import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def odoo_client_module():
    module = importlib.import_module("odoo_mcp.odoo_client")
    return importlib.reload(module)
