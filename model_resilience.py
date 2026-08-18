"""Compatibility wrapper for model provider resilience helpers.

The implementation lives in clashroyale_agent.ops.model_resilience. This root
module remains so existing scripts and tests can continue importing
model_resilience during the package migration.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_PATH = Path(__file__).resolve().parent / "src"
if str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

from clashroyale_agent.ops.model_resilience import (  # noqa: E402
    ModelCircuitOpenError,
    ModelProviderGuard,
    ModelStreamingUnavailableError,
)

__all__ = [
    "ModelCircuitOpenError",
    "ModelProviderGuard",
    "ModelStreamingUnavailableError",
]
