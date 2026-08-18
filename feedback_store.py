"""Compatibility wrapper for feedback storage helpers.

The implementation lives in clashroyale_agent.ops.feedback_store. This root
module remains so existing scripts and tests can continue importing
feedback_store during the package migration.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_PATH = Path(__file__).resolve().parent / "src"
if str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

from clashroyale_agent.ops.feedback_store import (  # noqa: E402
    FeedbackStore,
    RecentAnswerCache,
)

__all__ = ["FeedbackStore", "RecentAnswerCache"]
