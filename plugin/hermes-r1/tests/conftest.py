"""Make the hyphenated plugin directory importable as the package ``hermes_r1``."""
import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
FIXTURES = PLUGIN_DIR.parent.parent / "docs" / "fixtures"

if "hermes_r1" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "hermes_r1", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_r1"] = module
    spec.loader.exec_module(module)
