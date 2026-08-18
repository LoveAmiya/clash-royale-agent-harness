"""Runtime settings helpers with stable policy boundaries."""

from __future__ import annotations

from typing import Any

from .status import build_live_sample_settings_payload


class FixedLiveSampleTargetError(ValueError):
    """Raised when an operator tries to change the fixed weekly sample target."""

    status_code = 409


def fixed_live_sample_target(fixed_target_battles: int) -> int:
    """Return the production sample target used by official weekly snapshots."""
    return fixed_target_battles


def build_fixed_live_sample_settings(
    app: Any,
    *,
    fixed_target_battles: int,
    can_update_target: bool,
    refresh_status: str = "ready",
) -> dict:
    """Build the live-sample settings payload for a fixed official snapshot target."""
    target_battles = fixed_live_sample_target(fixed_target_battles)
    return build_live_sample_settings_payload(
        app,
        target_battles=target_battles,
        min_target_battles=target_battles,
        max_target_battles=target_battles,
        can_update_target=can_update_target,
        refresh_status=refresh_status,
    )


def reject_live_sample_target_update(
    target_battles: int,
    *,
    fixed_target_battles: int,
) -> None:
    """Reject attempts to change the fixed official weekly sample target."""
    raise FixedLiveSampleTargetError(
        f"weekly official sampling is fixed at {fixed_target_battles} battles"
    )


__all__ = [
    "FixedLiveSampleTargetError",
    "build_fixed_live_sample_settings",
    "fixed_live_sample_target",
    "reject_live_sample_target_update",
]
