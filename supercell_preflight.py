"""Compatibility entry point for the packaged collection preflight."""

from __future__ import annotations

import importlib
import sys

import app_config  # noqa: F401 - initializes the src package path for root runs.


_module = importlib.import_module("clashroyale_agent.collection.preflight")

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
