"""Startup and preheat facade for the API package migration."""

from __future__ import annotations

from clashroyale_agent.api.lifecycle import (
    initialize_runtime_data_state,
    initialize_runtime_services,
    shutdown_runtime_resources,
)
from clashroyale_agent.api.preheat import (
    RAGPreheatTarget,
    acquire_rag_preheat_lock,
    find_active_rag_retriever,
    find_reusable_rag_retriever,
    resolve_rag_preheat_target,
    run_rag_preheat_in_thread,
)
from clashroyale_agent.api.rag_preheat import RAGPreheatDependencies, preheat_rag_retriever


__all__ = [
    "RAGPreheatDependencies",
    "RAGPreheatTarget",
    "acquire_rag_preheat_lock",
    "find_active_rag_retriever",
    "find_reusable_rag_retriever",
    "initialize_runtime_data_state",
    "initialize_runtime_services",
    "preheat_rag_retriever",
    "resolve_rag_preheat_target",
    "run_rag_preheat_in_thread",
    "shutdown_runtime_resources",
]
