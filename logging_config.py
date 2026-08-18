"""Compatibility wrapper for logging helpers.

The implementation lives in `clashroyale_agent.ops.logging_config`. This root
module remains so existing scripts and tests can continue importing
`logging_config` during the package migration.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_PATH = Path(__file__).resolve().parent / "src"
if str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

from clashroyale_agent.ops.logging_config import (  # noqa: E402
    JsonFormatter,
    SecretRedactionFilter,
    configure_logging,
)

__all__ = ["JsonFormatter", "SecretRedactionFilter", "configure_logging"]
