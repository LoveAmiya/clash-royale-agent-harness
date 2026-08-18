"""Compatibility wrapper for runtime hardening and quota helpers.

The implementation lives in clashroyale_agent.ops.runtime_hardening. This root
module remains so existing scripts and tests can continue importing
runtime_hardening during the package migration.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_PATH = Path(__file__).resolve().parent / "src"
if str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

from clashroyale_agent.ops.runtime_hardening import (  # noqa: E402
    ProcessQuota,
    QuotaDecision,
    RedisProcessQuota,
    RequestBodyLimitMiddleware,
    RuntimeMetrics,
    authorize_admin,
    create_process_quota,
    normalize_request_id,
    redact_for_client,
    resolve_client_id,
)

__all__ = [
    "ProcessQuota",
    "QuotaDecision",
    "RedisProcessQuota",
    "RequestBodyLimitMiddleware",
    "RuntimeMetrics",
    "authorize_admin",
    "create_process_quota",
    "normalize_request_id",
    "redact_for_client",
    "resolve_client_id",
]
