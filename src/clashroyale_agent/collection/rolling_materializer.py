"""Materialize rolling dataset scopes as one atomic snapshot group."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import math
import os
import shutil
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from clashroyale_agent.collection.rolling_corpus import DATASET_SCOPES, RollingCorpusStore
from rag_document_policy import RAG_SOURCE_LIMITS, summarize_scope_documents
from structured_query import CARD_ALIAS_OVERRIDES, TOWER_DISPLAY_NAMES_ZH
from structured_stats import build_structured_stats
from clashroyale_agent.collection.materializer_primitives import (
    atomic_json as _atomic_json,
    docs_fingerprint as _docs_fingerprint,
    generation_id as _generation_id,
    iso as _iso,
    prune_group_versions as _prune_group_versions,
    sha256_file as _sha256_file,
)
from clashroyale_agent.collection.materializer_deltas import (
    materialize_meta_deltas as _materialize_meta_deltas_orchestrated,
)
from clashroyale_agent.collection.materializer_documents import (
    build_rag_documents as _build_rag_documents_orchestrated,
    validate_documents as _validate_documents_orchestrated,
)


GROUP_SCHEMA_VERSION = 2
DELTA_SCOPE_PAIRS = (
    ("7d", "d7_14"),
    ("d7_14", "d14_21"),
    ("d14_21", "d21_28"),
    ("d21_28", "d28_35"),
)


def _write_scope_source(
    store: RollingCorpusStore,
    *,
    scope: str,
    now: datetime,
    data_dir: Path,
    snapshot_id: str,
) -> tuple[Path, dict, str]:
    archive = data_dir / "snapshot_archives" / snapshot_id
    archive.mkdir(parents=True, exist_ok=True)
    aggregate = archive / "aggregates.sqlite"
    connection = sqlite3.connect(aggregate)
    connection.execute(
        "CREATE TABLE battles(sequence INTEGER PRIMARY KEY AUTOINCREMENT, battle_id TEXT UNIQUE, payload TEXT NOT NULL)"
    )
    count = 0
    content_digest = hashlib.sha256()
    with connection:
        for record in store.iter_scope_battles(scope, now=now):
            content_digest.update(str(record["battle_id"]).encode("utf-8"))
            content_digest.update(b"\n")
            connection.execute(
                "INSERT INTO battles(battle_id, payload) VALUES (?, ?)",
                (
                    record["battle_id"],
                    json.dumps(record, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            count += 1
    connection.close()
    summary = store.dataset_summary(scope, now=now)
    summary.update(
        {
            "snapshot_id": snapshot_id,
            "fetched_at": _iso(now),
            "sample_battles": count,
            "target_battles": count,
            "shortfall_battles": 0,
        }
    )
    _atomic_json(
        archive / "manifest.json",
        {"schema_version": 1, "snapshot_id": snapshot_id, "complete": True},
    )
    _atomic_json(archive / "collector_snapshot.json", summary)
    return aggregate, summary, content_digest.hexdigest()


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _scope_fingerprint(summary: dict) -> str:
    stable = dict(summary)
    stable.pop("snapshot_id", None)
    stable.pop("fetched_at", None)
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _scope_content_fingerprint(store: RollingCorpusStore, scope: str, now: datetime) -> str:
    digest = hashlib.sha256()
    for record in store.iter_scope_battles(scope, now=now):
        digest.update(str(record["battle_id"]).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _vector_input_fingerprint(documents: list[dict]) -> str:
    stable = [
        {
            "source_type": document.get("source_type"),
            "text": document.get("text"),
            "dataset_scope": document.get("metadata", {}).get("dataset_scope"),
        }
        for document in documents
    ]
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=True, sort_keys=False).encode("utf-8")
    ).hexdigest()


def _active_group_manifest(data_dir: Path) -> tuple[Path | None, dict | None]:
    pointer_path = Path(data_dir) / "active_snapshot_group.json"
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        group_id = str(pointer.get("snapshot_group_id") or "")
        if not group_id:
            return None, None
        group_root = Path(data_dir) / "snapshot_groups" / group_id
        manifest = json.loads((group_root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("fully_aligned") is not True:
            return None, None
        return group_root, manifest
    except (OSError, json.JSONDecodeError, AttributeError):
        return None, None


def _copy_scope_stats(target: sqlite3.Connection, source_path: Path, scope: str) -> None:
    attached_schema = "reuse_source"
    target.execute(
        f"ATTACH DATABASE ? AS {_quoted(attached_schema)}",
        (str(source_path),),
    )
    try:
        tables = [
            str(row[0])
            for row in target.execute(
                f"SELECT name FROM {_quoted(attached_schema)}.sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name NOT IN ('metadata', 'meta_delta', 'scope_metadata')"
            )
        ]
        for table in tables:
            columns = [str(row[1]) for row in target.execute(
                f"PRAGMA {_quoted(attached_schema)}.table_info({_quoted(table)})"
            )]
            if "dataset_scope" not in columns:
                continue
            target.execute(
                f"CREATE TABLE IF NOT EXISTS {_quoted(table)} AS "
                f"SELECT * FROM {_quoted(attached_schema)}.{_quoted(table)} WHERE 0"
            )
            target.execute(
                f"INSERT INTO {_quoted(table)} SELECT * FROM "
                f"{_quoted(attached_schema)}.{_quoted(table)} WHERE dataset_scope=?",
                (scope,),
            )
        target.commit()
    finally:
        target.execute(f"DETACH DATABASE {_quoted(attached_schema)}")


def _merge_scope_stats(target: sqlite3.Connection, source_path: Path, scope: str) -> None:
    attached_schema = "merge_source"
    target.execute(
        f"ATTACH DATABASE ? AS {_quoted(attached_schema)}",
        (str(source_path),),
    )
    try:
        tables = [
            str(row[0])
            for row in target.execute(
                f"SELECT name FROM {_quoted(attached_schema)}.sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='metadata'"
            )
        ]
        for table in tables:
            columns = target.execute(
                f"PRAGMA {_quoted(attached_schema)}.table_info({_quoted(table)})"
            ).fetchall()
            column_names = [str(column[1]) for column in columns]
            definitions = [
                f"{_quoted(str(column[1]))} {str(column[2]) or 'TEXT'}"
                + (" NOT NULL" if int(column[3]) else "")
                for column in columns
            ]
            target.execute(
                f"CREATE TABLE IF NOT EXISTS {_quoted(table)} "
                f"(dataset_scope TEXT NOT NULL, {', '.join(definitions)})"
            )
            selected = ",".join(_quoted(name) for name in column_names)
            target.execute(
                f"INSERT INTO {_quoted(table)} ({_quoted('dataset_scope')}, {selected}) "
                f"SELECT ?, {selected} FROM {_quoted(attached_schema)}.{_quoted(table)}",
                (scope,),
            )
        target.commit()
    finally:
        target.execute(f"DETACH DATABASE {_quoted(attached_schema)}")


def _create_group_indexes(connection: sqlite3.Connection) -> None:
    statements = (
        "CREATE INDEX idx_group_card ON card_stats(dataset_scope, card_name)",
        "CREATE INDEX idx_group_teammates ON card_teammates(dataset_scope, card_name, games DESC)",
        "CREATE INDEX idx_group_opponents ON card_opponents(dataset_scope, card_name, games DESC)",
        "CREATE INDEX idx_group_decks ON deck_stats(dataset_scope, deck_signature)",
        "CREATE INDEX idx_group_matchups ON matchup_stats(dataset_scope, deck_a_signature, deck_b_signature)",
        "CREATE INDEX idx_group_full_loadouts ON full_loadout_stats(dataset_scope, loadout_signature)",
        "CREATE INDEX idx_group_full_matchups ON full_loadout_matchup_stats(dataset_scope, loadout_a_signature, loadout_b_signature)",
        "CREATE INDEX idx_group_towers ON tower_stats(dataset_scope, appearances DESC)",
        "CREATE INDEX idx_group_evolutions ON evolution_stats(dataset_scope, appearances DESC)",
        "CREATE INDEX idx_group_elite ON elite_stats(dataset_scope, appearances DESC)",
        "CREATE INDEX idx_group_loadout_cards ON loadout_card_catalog(dataset_scope, card_id)",
        "CREATE INDEX idx_group_loadout_entities ON loadout_entity_stats(dataset_scope, entity_id)",
        "CREATE INDEX idx_group_archetypes ON archetype_stats(dataset_scope, games DESC)",
        "CREATE INDEX idx_group_archetype_decks ON archetype_decks(dataset_scope, archetype, games DESC)",
    )
    for statement in statements:
        connection.execute(statement)


def _wilson_interval(wins: int, losses: int, z: float = 1.96) -> tuple[float, float]:
    decisions = wins + losses
    if decisions <= 0:
        return 0.0, 0.0
    probability = wins / decisions
    z_squared = z * z
    denominator = 1 + z_squared / decisions
    centre = probability + z_squared / (2 * decisions)
    margin = z * math.sqrt(
        (probability * (1 - probability) + z_squared / (4 * decisions)) / decisions
    )
    return (
        max(0.0, (centre - margin) / denominator) * 100,
        min(1.0, (centre + margin) / denominator) * 100,
    )


def _materialize_meta_deltas(connection: sqlite3.Connection, datasets: dict[str, dict]) -> None:
    _materialize_meta_deltas_orchestrated(
        connection,
        datasets,
        dataset_scopes=DATASET_SCOPES,
        scope_pairs=DELTA_SCOPE_PAIRS,
        interval=_wilson_interval,
    )

def _rag_documents(connection: sqlite3.Connection, group_id: str, datasets: dict[str, dict]) -> list[dict]:
    return _build_rag_documents_orchestrated(connection, group_id, datasets)


def _validate_documents(documents: list[dict], group_id: str) -> dict:
    return _validate_documents_orchestrated(documents, group_id)

def build_snapshot_group(
    store: RollingCorpusStore,
    *,
    data_dir: Path,
    now: datetime | None = None,
    retriever_factory: Callable | None = None,
) -> dict:
    started_at = time.perf_counter()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    data_dir = Path(data_dir)
    group_id = _generation_id(store, current)
    groups_root = data_dir / "snapshot_groups"
    groups_root.mkdir(parents=True, exist_ok=True)
    destination = groups_root / group_id
    if destination.exists():
        return json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    store.begin_publication_generation(group_id, created_at=current)
    recent_weekly = int(
        store.connection.execute(
            """
            SELECT COUNT(*) FROM collection_batches
            WHERE status='accepted' AND batch_type='weekly_expanded'
              AND completed_at>? AND completed_at<=?
            """,
            (_iso(current - timedelta(days=35)), _iso(current)),
        ).fetchone()[0]
    )
    if recent_weekly < 1:
        failure = {"error_type": "ValueError", "message": "no accepted weekly expansion batch in the 35-day window"}
        store.finish_publication_generation(group_id, status="failed", manifest=failure)
        raise ValueError(failure["message"])
    candidate = Path(tempfile.mkdtemp(prefix=f".{group_id}.", dir=groups_root))
    previous_group_root, previous_manifest = _active_group_manifest(data_dir)
    group_stats = sqlite3.connect(candidate / "structured_stats.sqlite")
    group_stats.row_factory = sqlite3.Row
    group_stats.execute(
        """
        CREATE TABLE scope_metadata(
            dataset_scope TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL,
            window_started_at TEXT NOT NULL,
            window_ended_at TEXT NOT NULL,
            unique_battles INTEGER NOT NULL,
            provenance_json TEXT NOT NULL,
            counts_json TEXT NOT NULL
        )
        """
    )
    datasets: dict[str, dict] = {}
    try:
        scope_materialization_started_at = time.perf_counter()
        for scope in DATASET_SCOPES:
            snapshot_id = f"{group_id}--{scope}"
            scope_data = candidate / "scope_build" / scope
            summary = store.dataset_summary(scope, now=current)
            previous_dataset = (previous_manifest or {}).get("datasets", {}).get(scope, {})
            content_fingerprint = None
            if previous_group_root is not None:
                content_fingerprint = _scope_content_fingerprint(store, scope, current)
            reusable_scope = (
                previous_group_root is not None
                and previous_dataset.get("scope_content_fingerprint") == content_fingerprint
                and (previous_group_root / "structured_stats.sqlite").exists()
            )
            if reusable_scope:
                structured_counts = previous_dataset.get("structured_counts", {})
                _copy_scope_stats(
                    group_stats,
                    previous_group_root / "structured_stats.sqlite",
                    scope,
                )
            else:
                _, summary, written_content_fingerprint = _write_scope_source(
                    store,
                    scope=scope,
                    now=current,
                    data_dir=scope_data,
                    snapshot_id=snapshot_id,
                )
                structured_manifest = build_structured_stats(scope_data, snapshot_id)
                structured_counts = structured_manifest["counts"]
                source_stats = scope_data / "structured_stats" / snapshot_id / "stats.sqlite"
                _merge_scope_stats(group_stats, source_stats, scope)
                content_fingerprint = content_fingerprint or written_content_fingerprint
            dataset = {
                **store.dataset_summary(scope, now=current),
                "snapshot_id": snapshot_id,
                "scope_fingerprint": _scope_fingerprint(summary),
                "scope_content_fingerprint": content_fingerprint,
                "reused": reusable_scope,
                "structured_counts": structured_counts,
                "complete_loadout_ready": (
                    structured_counts.get("full_loadout_side_records", 0) > 0
                ),
                "entity_stats_ready": structured_counts.get("loadout_entities", 0) > 0,
                "delta_ready": False,
            }
            datasets[scope] = dataset
            group_stats.execute(
                "INSERT INTO scope_metadata VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    scope,
                    snapshot_id,
                    dataset["window_started_at"],
                    dataset["window_ended_at"],
                    dataset["unique_battles"],
                    json.dumps(dataset, ensure_ascii=False, sort_keys=True),
                    json.dumps(structured_counts, ensure_ascii=False, sort_keys=True),
                ),
            )
            shutil.rmtree(scope_data, ignore_errors=True)
        group_stats.commit()
        publication_timings_seconds = {
            "scope_materialization": time.perf_counter() - scope_materialization_started_at,
        }

        group_indexes_and_deltas_started_at = time.perf_counter()
        _create_group_indexes(group_stats)
        _materialize_meta_deltas(group_stats, datasets)
        for scope, dataset in datasets.items():
            group_stats.execute(
                "UPDATE scope_metadata SET provenance_json=? WHERE dataset_scope=?",
                (json.dumps(dataset, ensure_ascii=False, sort_keys=True), scope),
            )
        group_stats.commit()
        publication_timings_seconds["group_indexes_and_deltas"] = (
            time.perf_counter() - group_indexes_and_deltas_started_at
        )

        rag_documents_started_at = time.perf_counter()
        documents = _rag_documents(group_stats, group_id, datasets)
        validation = _validate_documents(documents, group_id)
        if not validation["passed"]:
            raise ValueError("rolling RAG document validation failed")
        rag_scope_counts, rag_scope_source_counts = summarize_scope_documents(documents, DATASET_SCOPES)
        _atomic_json(candidate / "rag_documents.json", documents)
        group_stats.close()
        group_stats = None
        publication_timings_seconds["rag_documents"] = time.perf_counter() - rag_documents_started_at

        if retriever_factory is None:
            from hybrid_retriever import HybridRetriever

            retriever_factory = HybridRetriever
        vector_fingerprint = _vector_input_fingerprint(documents)
        previous_qdrant = previous_group_root / "qdrant" if previous_group_root else None
        if (
            previous_qdrant is not None
            and previous_qdrant.is_dir()
            and (previous_manifest or {}).get("vector_input_fingerprint") == vector_fingerprint
        ):
            try:
                shutil.copytree(previous_qdrant, candidate / "qdrant")
            except OSError:
                # A live API can retain an embedded-Qdrant file lock; rebuild rather than
                # publishing a partial copy or blocking accepted facts.
                shutil.rmtree(candidate / "qdrant", ignore_errors=True)
        vector_index_validation_started_at = time.perf_counter()
        retriever = retriever_factory(
            documents,
            index_path=candidate / "qdrant",
            lazy_scope_bm25=True,
            bm25_scope_cache_size=2,
        )
        try:
            if not getattr(retriever, "dense_available", False):
                raise RuntimeError("rolling snapshot group requires a ready local vector index")
            if getattr(retriever, "docs_fingerprint", None) != validation["docs_fingerprint"]:
                raise RuntimeError("rolling vector index fingerprint mismatch")
            probe_failures = []
            for scope in DATASET_SCOPES:
                results = retriever.hybrid_search(
                    f"Path of Legend dataset overview {scope}",
                    final_top_k=5,
                    dataset_scope=scope,
                )
                if not results or any(
                    item.get("doc", {}).get("metadata", {}).get("dataset_scope") != scope
                    for item in results
                ):
                    probe_failures.append(scope)
            if probe_failures:
                raise RuntimeError("rolling retrieval scope probe failed: " + ",".join(probe_failures))
        finally:
            retriever.close()
        publication_timings_seconds["vector_index_validation"] = (
            time.perf_counter() - vector_index_validation_started_at
        )

        stats_path = candidate / "structured_stats.sqlite"
        publish_and_cleanup_started_at = time.perf_counter()
        manifest = {
            "schema_version": GROUP_SCHEMA_VERSION,
            "snapshot_group_id": group_id,
            "published_at": _iso(current),
            "default_dataset_scope": "7d_all",
            "datasets": datasets,
            "structured_stats_fingerprint": _sha256_file(stats_path),
            "rag_docs_fingerprint": validation["docs_fingerprint"],
            "rag_document_count": validation["document_count"],
            "rag_source_counts": validation["source_counts"],
            "rag_scope_counts": rag_scope_counts,
            "rag_scope_source_counts": rag_scope_source_counts,
            "index_docs_fingerprint": validation["docs_fingerprint"],
            "vector_input_fingerprint": vector_fingerprint,
            "fully_aligned": True,
            "cost_boundaries": {
                "cloud_llm_calls": 0,
                "cloud_embedding_calls": 0,
                "local_embedding_index_builds": 1,
            },
            "publication_timings_seconds": {
                **publication_timings_seconds,
                "publish_and_cleanup": 0.0,
                "total": 0.0,
            },
        }
        removed_groups = _prune_group_versions(data_dir, group_id, keep=2)
        manifest["removed_snapshot_group_ids"] = removed_groups
        publication_timings_seconds["publish_and_cleanup"] = (
            time.perf_counter() - publish_and_cleanup_started_at
        )
        publication_timings_seconds["total"] = time.perf_counter() - started_at
        manifest["publication_timings_seconds"] = publication_timings_seconds
        _atomic_json(candidate / "manifest.json", manifest)
        os.replace(candidate, destination)
        _atomic_json(
            data_dir / "active_snapshot_group.json",
            {
                "schema_version": 1,
                "snapshot_group_id": group_id,
                "published_at": manifest["published_at"],
                "default_dataset_scope": manifest["default_dataset_scope"],
            },
        )
        store.finish_publication_generation(
            group_id,
            status="published",
            manifest=manifest,
            published_at=current,
        )
        return manifest
    except Exception as exc:
        if group_stats is not None:
            group_stats.close()
        shutil.rmtree(candidate, ignore_errors=True)
        store.finish_publication_generation(
            group_id,
            status="failed",
            manifest={"error_type": type(exc).__name__, "message": str(exc)},
        )
        raise

