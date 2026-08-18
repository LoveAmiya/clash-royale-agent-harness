"""Compatibility entry point for the packaged rolling snapshot materializer."""

from __future__ import annotations

import importlib
import sys

import app_config  # noqa: F401 - initializes the src package path for root runs.


_module = importlib.import_module("clashroyale_agent.collection.rolling_materializer")

globals().update(
    {
        name: value
        for name, value in vars(_module).items()
        if not name.startswith("__")
    }
)
sys.modules[__name__] = _module
