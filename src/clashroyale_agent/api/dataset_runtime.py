"""Runtime coordination for rolling dataset catalog and dependency resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from structured_query import StructuredQueryError


@dataclass(frozen=True)
class DatasetRuntimeDependencies:
    """Runtime configuration and implementations for dataset-scoped access."""

    data_dir: Path
    dataset_scopes: Sequence[str]
    default_dataset_scope: str
    dataset_window_definitions: Mapping[str, Mapping[str, object]]
    rag_source_limits: Mapping[str, int]
    rag_document_count_semantics: str
    rag_scope_count_semantics: str
    retrieval: Mapping[str, object]
    saturated_source_types: Callable[[Mapping[str, int] | None], Sequence[str]]
    summarize_scope_documents: Callable[
        [list[dict], Sequence[str]],
        tuple[dict[str, int], dict[str, dict[str, int]]],
    ]
    validate_dataset_scope: Callable[..., str]
    load_active_snapshot_group_manifest: Callable[..., dict | None]
    rag_scope_stats_for_manifest: Callable[..., tuple[dict[str, int], dict[str, dict[str, int]]]]
    build_dataset_catalog_payload: Callable[..., dict]
    resolve_structured_group_repository: Callable[..., Any]
    resolve_official_structured_repository: Callable[..., Any]
    resolve_rolling_dataset_retriever: Callable[..., Any]
    pointer_loader: Callable[[Path], Any]
    structured_repository_cls: type
    retriever_cls: type
    lock_factory: Callable[[], Any]
    ensure_retriever: Callable[[Any], Any | None]
    logger: Any


def validate_scope(
    dataset_scope: str | None,
    *,
    dependencies: DatasetRuntimeDependencies,
) -> str:
    """Normalize one dataset scope against the configured rolling scopes."""
    return dependencies.validate_dataset_scope(
        dataset_scope,
        default_scope=dependencies.default_dataset_scope,
        allowed_scopes=dependencies.dataset_scopes,
    )


def load_active_manifest(
    *,
    dependencies: DatasetRuntimeDependencies,
) -> dict | None:
    """Load the active group manifest using the configured scope contract."""
    return dependencies.load_active_snapshot_group_manifest(
        dependencies.data_dir,
        allowed_scopes=dependencies.dataset_scopes,
    )


def rag_scope_stats(
    app: Any,
    manifest: dict,
    *,
    dependencies: DatasetRuntimeDependencies,
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Read published RAG scope counts or derive legacy counts behind one boundary."""
    return dependencies.rag_scope_stats_for_manifest(
        app,
        manifest,
        dependencies.data_dir,
        dataset_scopes=dependencies.dataset_scopes,
        summarize_scope_documents=dependencies.summarize_scope_documents,
        warn=lambda snapshot_group_id: dependencies.logger.warning(
            "unable to derive legacy RAG scope counts snapshot_group_id=%s",
            snapshot_group_id,
        ),
    )


def get_dataset_catalog(
    app: Any,
    *,
    dependencies: DatasetRuntimeDependencies,
) -> dict:
    """Return the stable rolling dataset catalog payload."""
    manifest = load_active_manifest(dependencies=dependencies)
    if manifest is None:
        return dependencies.build_dataset_catalog_payload(
            None,
            dataset_scopes=dependencies.dataset_scopes,
            default_dataset_scope=dependencies.default_dataset_scope,
            dataset_window_definitions=dependencies.dataset_window_definitions,
            rag_scope_counts={},
            rag_scope_source_counts={},
            rag_source_limits=dependencies.rag_source_limits,
            rag_document_count_semantics=dependencies.rag_document_count_semantics,
            rag_scope_count_semantics=dependencies.rag_scope_count_semantics,
            retrieval={},
            saturated_source_types=dependencies.saturated_source_types,
        )
    rag_scope_counts, rag_scope_source_counts = rag_scope_stats(
        app,
        manifest,
        dependencies=dependencies,
    )
    return dependencies.build_dataset_catalog_payload(
        manifest,
        dataset_scopes=dependencies.dataset_scopes,
        default_dataset_scope=dependencies.default_dataset_scope,
        dataset_window_definitions=dependencies.dataset_window_definitions,
        rag_scope_counts=rag_scope_counts,
        rag_scope_source_counts=rag_scope_source_counts,
        rag_source_limits=dependencies.rag_source_limits,
        rag_document_count_semantics=dependencies.rag_document_count_semantics,
        rag_scope_count_semantics=dependencies.rag_scope_count_semantics,
        retrieval=dependencies.retrieval,
        saturated_source_types=dependencies.saturated_source_types,
    )


def get_structured_repository(
    app: Any,
    dataset_scope: str | None,
    *,
    dependencies: DatasetRuntimeDependencies,
) -> Any:
    """Resolve a structured repository for either rolling or official data."""
    scope = validate_scope(dataset_scope, dependencies=dependencies)
    group_manifest = load_active_manifest(dependencies=dependencies)
    if group_manifest is not None:
        return dependencies.resolve_structured_group_repository(
            app,
            dependencies.data_dir,
            group_manifest["snapshot_group_id"],
            scope,
            repository_cls=dependencies.structured_repository_cls,
        )
    if scope != dependencies.default_dataset_scope:
        raise StructuredQueryError(
            "DATASET_SCOPE_NOT_READY",
            "The requested rolling dataset scope has not been published yet.",
            status_code=503,
            details={"dataset_scope": scope},
        )
    return dependencies.resolve_official_structured_repository(
        app,
        dependencies.data_dir,
        pointer_loader=dependencies.pointer_loader,
        repository_cls=dependencies.structured_repository_cls,
    )


def ensure_dataset_retriever(
    app: Any,
    dataset_scope: str | None,
    *,
    dependencies: DatasetRuntimeDependencies,
) -> Any | None:
    """Return the active RAG retriever for one published dataset scope."""
    scope = validate_scope(dataset_scope, dependencies=dependencies)
    manifest = load_active_manifest(dependencies=dependencies)
    if manifest is None:
        if scope != dependencies.default_dataset_scope:
            raise StructuredQueryError(
                "DATASET_SCOPE_NOT_READY",
                "The requested rolling dataset scope has not been published yet.",
                status_code=503,
                details={"dataset_scope": scope},
            )
        return dependencies.ensure_retriever(app)
    return dependencies.resolve_rolling_dataset_retriever(
        app,
        dependencies.data_dir,
        manifest,
        scope,
        retriever_cls=dependencies.retriever_cls,
        lock_factory=dependencies.lock_factory,
    )


__all__ = [
    "DatasetRuntimeDependencies",
    "ensure_dataset_retriever",
    "get_dataset_catalog",
    "get_structured_repository",
    "load_active_manifest",
    "rag_scope_stats",
    "validate_scope",
]
