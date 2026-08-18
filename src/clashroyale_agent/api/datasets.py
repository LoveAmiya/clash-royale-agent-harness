"""Dataset scope helpers for structured API routes."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
import json
from pathlib import Path
from typing import Any

from structured_query import StructuredQueryError


def dataset_scope_parts(
    scope: str,
    *,
    dataset_window_definitions: Mapping[str, Mapping[str, object]],
) -> tuple[str, str]:
    """Split a rolling dataset scope into window prefix and level suffix."""
    prefix = next(
        (
            candidate
            for candidate in dataset_window_definitions
            if scope.startswith(f"{candidate}_")
        ),
        "",
    )
    return prefix, scope[len(prefix) + 1 :] if prefix else scope


def dataset_scope_display_name(
    scope: str,
    *,
    dataset_window_definitions: Mapping[str, Mapping[str, object]],
) -> str:
    """Return the public display label for a rolling dataset scope."""
    window, level = dataset_scope_parts(scope, dataset_window_definitions=dataset_window_definitions)
    window_labels = {
        "7d": "\u6700\u8fd17\u5929",
        "d7_14": "7\u81f314\u5929\u524d",
        "d14_21": "14\u81f321\u5929\u524d",
        "d21_28": "21\u81f328\u5929\u524d",
        "d28_35": "28\u81f335\u5929\u524d",
        "35d": "\u6700\u8fd135\u5929",
    }
    level_name = "\u5168\u91cf" if level == "all" else f"\u524d{level.rsplit('_', 1)[-1]}"
    return f"{window_labels.get(window, window)} \u00b7 {level_name}"


def unavailable_dataset_payload(
    scope: str,
    *,
    dataset_window_definitions: Mapping[str, Mapping[str, object]],
) -> dict:
    """Build the stable unavailable dataset entry used by the catalog route."""
    prefix, _ = dataset_scope_parts(scope, dataset_window_definitions=dataset_window_definitions)
    definition = dataset_window_definitions[prefix]
    return {
        "dataset_scope": scope,
        "name": dataset_scope_display_name(
            scope,
            dataset_window_definitions=dataset_window_definitions,
        ),
        "window_days": int(definition["end_offset_days"]) - int(definition["start_offset_days"]),
        "window_kind": definition["window_kind"],
        "window_start_offset_days": definition["start_offset_days"],
        "window_end_offset_days": definition["end_offset_days"],
        "rank_limit": int(scope.rsplit("_", 1)[-1]) if "_top_" in scope else None,
        "ready": False,
        "complete_loadout_ready": False,
        "entity_stats_ready": False,
        "delta_ready": False,
        "rag_document_count": None,
        "rag_source_counts": {},
        "rag_saturated_source_types": [],
    }


def build_dataset_catalog_payload(
    manifest: Mapping[str, Any] | None,
    *,
    dataset_scopes: Sequence[str],
    default_dataset_scope: str,
    dataset_window_definitions: Mapping[str, Mapping[str, object]],
    rag_scope_counts: Mapping[str, int],
    rag_scope_source_counts: Mapping[str, Mapping[str, int]],
    rag_source_limits: Mapping[str, int],
    rag_document_count_semantics: str,
    rag_scope_count_semantics: str,
    retrieval: Mapping[str, object],
    saturated_source_types: Callable[[Mapping[str, int] | None], Sequence[str]],
) -> dict:
    """Build the public dataset catalog response without depending on FastAPI state."""
    def unavailable_dataset(scope: str) -> dict:
        return unavailable_dataset_payload(
            scope,
            dataset_window_definitions=dataset_window_definitions,
        )

    if manifest is None:
        return {
            "snapshot_group_id": None,
            "default_dataset_scope": default_dataset_scope,
            "datasets": [unavailable_dataset(scope) for scope in dataset_scopes],
        }

    manifest_datasets = manifest.get("datasets")
    datasets = manifest_datasets if isinstance(manifest_datasets, Mapping) else {}

    return {
        "snapshot_group_id": manifest["snapshot_group_id"],
        "published_at": manifest.get("published_at"),
        "default_dataset_scope": manifest.get("default_dataset_scope", default_dataset_scope),
        "rag": {
            "status": "ready",
            "document_count": manifest.get("rag_document_count"),
            "scope_counts": dict(rag_scope_counts),
            "scope_source_counts": {
                scope: dict(counts)
                for scope, counts in rag_scope_source_counts.items()
            },
            "source_limits": rag_source_limits,
            "document_count_semantics": rag_document_count_semantics,
            "scope_document_count_semantics": rag_scope_count_semantics,
            "global_count_includes_scope_duplicates": True,
            "fully_aligned": manifest.get("fully_aligned") is True,
            "retrieval": dict(retrieval),
        },
        "datasets": [
            _catalog_dataset_payload(
                scope,
                dataset=datasets.get(scope),
                dataset_window_definitions=dataset_window_definitions,
                rag_scope_counts=rag_scope_counts,
                rag_scope_source_counts=rag_scope_source_counts,
                saturated_source_types=saturated_source_types,
            )
            for scope in dataset_scopes
        ],
    }


def _catalog_dataset_payload(
    scope: str,
    *,
    dataset: object,
    dataset_window_definitions: Mapping[str, Mapping[str, object]],
    rag_scope_counts: Mapping[str, int],
    rag_scope_source_counts: Mapping[str, Mapping[str, int]],
    saturated_source_types: Callable[[Mapping[str, int] | None], Sequence[str]],
) -> dict:
    if not isinstance(dataset, Mapping):
        return unavailable_dataset_payload(
            scope,
            dataset_window_definitions=dataset_window_definitions,
        )

    source_counts = dict(rag_scope_source_counts.get(scope, {}))
    structured_counts = dataset.get("structured_counts")
    structured_counts = structured_counts if isinstance(structured_counts, Mapping) else {}
    return {
        **unavailable_dataset_payload(
            scope,
            dataset_window_definitions=dataset_window_definitions,
        ),
        **dataset,
        "dataset_scope": scope,
        "name": dataset_scope_display_name(
            scope,
            dataset_window_definitions=dataset_window_definitions,
        ),
        "rag_document_count": rag_scope_counts.get(scope),
        "rag_source_counts": source_counts,
        "rag_saturated_source_types": list(saturated_source_types(source_counts)),
        "ready": (
            dataset.get("ready") is True
            if "ready" in dataset
            else int(dataset.get("unique_battles") or 0) > 0
        ),
        "complete_loadout_ready": (
            dataset.get("complete_loadout_ready") is True
            if "complete_loadout_ready" in dataset
            else int(structured_counts.get("full_loadout_side_records") or 0) > 0
        ),
        "entity_stats_ready": dataset.get("entity_stats_ready") is True,
        "delta_ready": dataset.get("delta_ready") is True,
    }


def validate_dataset_scope(
    dataset_scope: str | None,
    *,
    default_scope: str,
    allowed_scopes: Sequence[str],
) -> str:
    """Normalize and validate a published rolling dataset scope."""
    scope = str(dataset_scope or default_scope).strip()
    if scope not in allowed_scopes:
        raise StructuredQueryError(
            "INVALID_DATASET_SCOPE",
            "dataset_scope must be one of the published rolling dataset scopes.",
            details={"dataset_scope": scope, "allowed": list(allowed_scopes)},
        )
    return scope


def load_active_snapshot_group_manifest(
    data_dir: Path,
    *,
    allowed_scopes: Sequence[str],
) -> dict | None:
    """Load and validate the active rolling snapshot group manifest."""
    root = Path(data_dir)
    pointer_path = root / "active_snapshot_group.json"
    if not pointer_path.is_file():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        group_id = str(pointer.get("snapshot_group_id") or "").strip()
        manifest_path = root / "snapshot_groups" / group_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        raise StructuredQueryError(
            "DATASET_SCOPE_NOT_READY",
            "The active rolling snapshot group is incomplete.",
            status_code=503,
        ) from exc
    if (
        not group_id
        or manifest.get("snapshot_group_id") != group_id
        or manifest.get("fully_aligned") is not True
        or not set((manifest.get("datasets") or {}).keys())
        or not set((manifest.get("datasets") or {}).keys()).issubset(set(allowed_scopes))
        or manifest.get("rag_docs_fingerprint") != manifest.get("index_docs_fingerprint")
    ):
        raise StructuredQueryError(
            "DATASET_SCOPE_NOT_READY",
            "The active rolling snapshot group failed alignment validation.",
            status_code=503,
            details={"snapshot_group_id": group_id or None},
        )
    return manifest


def rag_scope_stats_for_manifest(
    app: Any,
    manifest: dict,
    data_dir: Path,
    *,
    dataset_scopes: Sequence[str],
    summarize_scope_documents: Callable[
        [list[dict], Sequence[str]],
        tuple[dict[str, int], dict[str, dict[str, int]]],
    ],
    warn: Callable[[object], None] | None = None,
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Return per-scope RAG document counts for a rolling snapshot group."""
    published_counts = manifest.get("rag_scope_counts")
    published_source_counts = manifest.get("rag_scope_source_counts")
    if isinstance(published_counts, dict) and isinstance(published_source_counts, dict):
        counts = {
            scope: max(0, int(published_counts.get(scope) or 0))
            for scope in dataset_scopes
        }
        source_counts = {
            scope: {
                str(source_type): max(0, int(count or 0))
                for source_type, count in (published_source_counts.get(scope) or {}).items()
            }
            for scope in dataset_scopes
        }
        return counts, source_counts

    cache_key = (
        str(Path(data_dir).resolve()),
        str(manifest.get("snapshot_group_id") or ""),
        str(manifest.get("rag_docs_fingerprint") or ""),
    )
    cached = getattr(app.state, "rag_scope_stats_cache", None)
    if isinstance(cached, dict) and cached.get("key") == cache_key:
        return dict(cached.get("counts") or {}), {
            scope: dict(counts)
            for scope, counts in (cached.get("source_counts") or {}).items()
        }

    documents_path = (
        Path(data_dir)
        / "snapshot_groups"
        / str(manifest.get("snapshot_group_id") or "")
        / "rag_documents.json"
    )
    try:
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
        if not isinstance(documents, list):
            raise ValueError("rolling RAG documents must be a list")
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        if warn is not None:
            warn(manifest.get("snapshot_group_id"))
        documents = []

    derived_counts, derived_source_counts = summarize_scope_documents(documents, dataset_scopes)
    counts = (
        {
            scope: max(0, int(published_counts.get(scope) or 0))
            for scope in dataset_scopes
        }
        if isinstance(published_counts, dict)
        else derived_counts
    )
    source_counts = (
        {
            scope: {
                str(source_type): max(0, int(count or 0))
                for source_type, count in (published_source_counts.get(scope) or {}).items()
            }
            for scope in dataset_scopes
        }
        if isinstance(published_source_counts, dict)
        else derived_source_counts
    )
    app.state.rag_scope_stats_cache = {
        "key": cache_key,
        "counts": counts,
        "source_counts": source_counts,
    }
    return dict(counts), {scope: dict(value) for scope, value in source_counts.items()}


def resolve_structured_group_repository(
    app: Any,
    data_dir: Path,
    group_id: str,
    scope: str,
    *,
    repository_cls: type,
) -> Any:
    """Return a cached structured repository for a rolling snapshot group scope."""
    repositories = getattr(app.state, "structured_group_repositories", None)
    if not isinstance(repositories, dict):
        repositories = {}
        app.state.structured_group_repositories = repositories

    cache_key = (group_id, scope)
    repository = repositories.get(cache_key)
    if not isinstance(repository, repository_cls):
        repository = repository_cls.for_snapshot_group(data_dir, group_id, scope)
        repositories.clear()
        repositories[cache_key] = repository
    return repository


def resolve_official_structured_repository(
    app: Any,
    data_dir: Path,
    *,
    pointer_loader: Callable[[Path], Any],
    repository_cls: type,
) -> Any:
    """Return the cached structured repository for the active official snapshot."""
    snapshot = getattr(app.state, "live_snapshot", None)
    snapshot_id = str(snapshot.get("snapshot_id") or "") if isinstance(snapshot, Mapping) else ""
    if not snapshot_id:
        try:
            pointer = pointer_loader(Path(data_dir) / "official_snapshot_pointer.json")
            snapshot_id = str(pointer.get("snapshot_id") or "") if isinstance(pointer, Mapping) else ""
        except (OSError, json.JSONDecodeError):
            snapshot_id = ""
    if not snapshot_id:
        raise StructuredQueryError(
            "STRUCTURED_INDEX_UNAVAILABLE",
            "No active official snapshot is available for structured queries.",
            status_code=503,
        )

    repository = getattr(app.state, "structured_repository", None)
    if not isinstance(repository, repository_cls) or repository.snapshot_id != snapshot_id:
        repository = repository_cls(data_dir, snapshot_id)
        app.state.structured_repository = repository
    return repository


def resolve_rolling_dataset_retriever(
    app: Any,
    data_dir: Path,
    manifest: Mapping[str, Any],
    scope: str,
    *,
    retriever_cls: type,
    lock_factory: Callable[[], Any],
) -> Any:
    """Return the cached rolling RAG retriever for an active snapshot group."""
    group_id = manifest["snapshot_group_id"]
    retriever = getattr(app.state, "rolling_retriever", None)
    if getattr(app.state, "rolling_retriever_group_id", None) == group_id and retriever is not None:
        return retriever

    lock = getattr(app.state, "rolling_retriever_lock", None)
    if lock is None:
        lock = lock_factory()
        app.state.rolling_retriever_lock = lock

    with lock:
        retriever = getattr(app.state, "rolling_retriever", None)
        if getattr(app.state, "rolling_retriever_group_id", None) == group_id and retriever is not None:
            return retriever

        group_dir = Path(data_dir) / "snapshot_groups" / str(group_id)
        try:
            documents = json.loads((group_dir / "rag_documents.json").read_text(encoding="utf-8"))
            candidate = retriever_cls(
                documents,
                index_path=group_dir / "qdrant",
                lazy_scope_bm25=True,
                bm25_scope_cache_size=2,
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise StructuredQueryError(
                "DATASET_SCOPE_NOT_READY",
                "The rolling RAG index could not be loaded.",
                status_code=503,
                details={"snapshot_group_id": group_id, "dataset_scope": scope},
            ) from exc

        expected = manifest.get("rag_docs_fingerprint")
        if not candidate.dense_available or candidate.docs_fingerprint != expected:
            candidate.close()
            raise StructuredQueryError(
                "DATASET_SCOPE_NOT_READY",
                "The rolling RAG index is not aligned with its documents.",
                status_code=503,
                details={"snapshot_group_id": group_id, "dataset_scope": scope},
            )

        previous = getattr(app.state, "rolling_retriever", None)
        app.state.rolling_retriever = candidate
        app.state.rolling_retriever_group_id = group_id
        if previous is not None and previous is not candidate:
            previous.close()
        return candidate


__all__ = [
    "build_dataset_catalog_payload",
    "dataset_scope_display_name",
    "dataset_scope_parts",
    "load_active_snapshot_group_manifest",
    "rag_scope_stats_for_manifest",
    "resolve_official_structured_repository",
    "resolve_rolling_dataset_retriever",
    "resolve_structured_group_repository",
    "unavailable_dataset_payload",
    "validate_dataset_scope",
]
