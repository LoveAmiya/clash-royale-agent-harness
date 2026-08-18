"""Compatibility alias for application configuration.

The implementation lives in clashroyale_agent.ops.app_config. This root module
keeps old imports and module-level patching semantics working during the
package migration.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_SRC_PATH = Path(__file__).resolve().parent / "src"
if str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

_module = importlib.import_module("clashroyale_agent.ops.app_config")
globals().update({name: getattr(_module, name) for name in dir(_module) if not name.startswith("__")})
__all__ = list(getattr(_module, "__all__", []))
sys.modules[__name__] = _module
