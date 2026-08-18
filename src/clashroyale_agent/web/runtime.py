"""Application construction and startup for the browser UI service."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI


def create_web_app() -> FastAPI:
    return FastAPI(title="CR Agent Web UI")


def run_web_app(*, host: str, port: int, uvicorn_module: Any) -> None:
    uvicorn_module.run("web_app:app", host=host, port=port, reload=False)


__all__ = ["create_web_app", "run_web_app"]
