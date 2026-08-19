"""Feedback API route registration helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from clashroyale_agent.api.admin import build_admin_dependency
from clashroyale_agent.api.schemas import FeedbackRequest


def register_feedback_routes(
    app: FastAPI,
    *,
    get_recent_answers: Callable[[], Any],
    get_feedback_store: Callable[[], Any],
    admin_api_key: str | None | Callable[[], str | None] = None,
    authorize_admin: Callable[[str | None, str | None], bool] | None = None,
) -> None:
    """Register feedback routes on an app."""
    require_admin = build_admin_dependency(
        admin_api_key=admin_api_key,
        authorize_admin=authorize_admin,
    )

    @app.post("/feedback")
    async def submit_feedback(payload: FeedbackRequest):
        cache = get_recent_answers()
        store = get_feedback_store()
        if store is None:
            raise HTTPException(status_code=503, detail="feedback service is initializing")
        try:
            answer = (
                cache.get(payload.request_id)
                if cache is not None
                else None
            ) or store.get_answer(payload.request_id)
            record = store.submit(
                answer=answer,
                rating=payload.rating,
                correction=payload.correction,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"status": "recorded", **record}

    @app.get("/feedback/stats")
    async def feedback_stats(_admin: None = Depends(require_admin)):
        store = get_feedback_store()
        if store is None:
            raise HTTPException(status_code=503, detail="feedback service is initializing")
        return store.stats()


__all__ = ["register_feedback_routes"]
