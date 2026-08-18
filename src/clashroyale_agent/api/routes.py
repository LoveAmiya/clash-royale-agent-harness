"""Route registration facade for the API package migration."""

from __future__ import annotations

from clashroyale_agent.api.feedback_routes import register_feedback_routes
from clashroyale_agent.api.process_routes import ProcessEndpoint, register_process_routes
from clashroyale_agent.api.settings_routes import register_live_sample_settings_routes
from clashroyale_agent.api.snapshot_routes import register_snapshot_status_routes
from clashroyale_agent.api.status_routes import register_status_routes
from clashroyale_agent.api.structured_routes import register_structured_api_routes


__all__ = [
    "ProcessEndpoint",
    "register_feedback_routes",
    "register_live_sample_settings_routes",
    "register_process_routes",
    "register_snapshot_status_routes",
    "register_status_routes",
    "register_structured_api_routes",
]
