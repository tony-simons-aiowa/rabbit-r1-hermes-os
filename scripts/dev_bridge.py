"""Run the bridge straight from the repo, without installing the plugin into Hermes.

    uv run --project plugin/hermes-r1 python scripts/dev_bridge.py --mock

All arguments are passed to the bridge (see `--help`).
"""
import importlib
import importlib.util
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugin" / "hermes-r1"

spec = importlib.util.spec_from_file_location(
    "hermes_r1", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
)
module = importlib.util.module_from_spec(spec)
sys.modules["hermes_r1"] = module
spec.loader.exec_module(module)

if __name__ == "__main__":
    sys.exit(importlib.import_module("hermes_r1.bridge.__main__").main(sys.argv[1:]))
