"""Compatibility wrapper for request-scoped runtime events.

The implementation lives in `clashroyale_agent.ops.runtime_events`. This root
module remains so existing scripts and tests can continue importing
`runtime_events` during the package migration.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_PATH = Path(__file__).resolve().parent / "src"
if str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

from clashroyale_agent.ops.runtime_events import RuntimeEventEmitter  # noqa: E402

__all__ = ["RuntimeEventEmitter"]
