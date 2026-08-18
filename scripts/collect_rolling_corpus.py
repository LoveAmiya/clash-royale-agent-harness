"""Compatibility entry point for the packaged rolling collector."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app_config  # noqa: F401 - initializes the src package path for root runs.


_module = importlib.import_module("clashroyale_agent.collection.rolling_collector")

if __name__ == "__main__":
    sys.exit(_module.main())

globals().update(
    {
        name: value
        for name, value in vars(_module).items()
        if not name.startswith("__")
    }
)
sys.modules[__name__] = _module
