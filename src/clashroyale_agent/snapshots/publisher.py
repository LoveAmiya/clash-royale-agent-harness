"""Atomic publication orchestration for complete official snapshots."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable


def publish_daily_snapshot(
    snapshot: dict,
    data_dir: Path,
    *,
    is_complete: Callable[[dict], bool],
    snapshot_id: Callable[[dict], str],
    now_utc: Callable[[], str],
    with_snapshot_metadata: Callable[[object, dict], list[dict]],
    raw_record_type: type,
    build_card_deck_stats: Callable[..., dict],
    build_documents: Callable[[dict], list[dict]],
    validate_documents: Callable[[dict, list[dict]], dict],
    atomic_write: Callable[[Path, Any], None],
    archive_snapshot: Callable[[Path, dict, list[dict]], None],
    remove_tree: Callable[[Path], None],
    scope_name: str,
    scope_contract: str,
    battle_type: str,
    archive_dir_name: str,
    snapshot_file_name: str,
    pointer_file_name: str,
) -> dict:
    """Publish derived datasets before atomically replacing the canonical snapshot."""
    if not is_complete(snapshot):
        raise ValueError("refusing to publish an incomplete official weekly snapshot")

    if snapshot.get("collection_scope") == scope_name:
        if snapshot.get("scope_contract") != scope_contract:
            raise ValueError("refusing to publish a snapshot with an invalid Path of Legend scope contract")
        scope_record_count = 0
        invalid_scope_records = 0
        for record in snapshot.get("raw_battles", []):
            scope_record_count += 1
            if not isinstance(record, dict) or record.get("battle_type") != battle_type:
                invalid_scope_records += 1
        if scope_record_count != int(snapshot.get("sample_battles") or 0):
            raise ValueError("refusing to publish a Path of Legend snapshot with an incomplete raw corpus")
        if invalid_scope_records:
            raise ValueError(
                "refusing to publish a Path of Legend snapshot containing "
                f"{invalid_scope_records} out-of-scope raw battles"
            )

    published = dict(snapshot)
    published["snapshot_id"] = snapshot_id(published)
    published.setdefault("published_at", now_utc())
    published["cards_meta"] = with_snapshot_metadata(published.get("cards_meta"), published)
    published["top_decks"] = with_snapshot_metadata(published.get("top_decks"), published)
    published["deck_matchups"] = with_snapshot_metadata(published.get("deck_matchups"), published)
    raw_battles = published.get("raw_battles")
    streamed = isinstance(raw_battles, raw_record_type)
    if streamed and len(raw_battles) != int(published.get("sample_battles") or 0):
        raise ValueError("refusing to publish a streamed snapshot with an incomplete raw corpus")
    aggregate_source = published.get("_aggregate_store_path")
    if streamed:
        if not isinstance(aggregate_source, str) or not Path(aggregate_source).is_file():
            raise ValueError("refusing to publish a streamed snapshot without its exact aggregate store")
        published["aggregate_store"] = {
            "schema_version": 1,
            "canonical_file": f"{archive_dir_name}/{published['snapshot_id']}/aggregates.sqlite",
            "archive_file": "aggregates.sqlite",
            "exact_matchups": int(published.get("collection_metrics", {}).get("exact_matchups_stored", 0)),
        }
        published["raw_battles_storage"] = {
            "format": "canonical_json_array",
            "canonical_file": snapshot_file_name,
            "record_count": len(raw_battles),
        }
    if not isinstance(published.get("card_deck_stats"), dict):
        published["card_deck_stats"] = build_card_deck_stats(
            list(published.get("raw_battles", [])),
            fetched_at=str(published.get("fetched_at") or ""),
            sample_battles=int(published.get("sample_battles") or 0),
            target_battles=int(published.get("target_battles") or 0),
        )
    documents = build_documents(published)
    published["rag_document_counts"] = dict(Counter(document["source_type"] for document in documents))
    validation = validate_documents(published, documents)
    if not validation["passed"]:
        raise ValueError("refusing to publish invalid RAG evidence: " + ", ".join(validation["failures"]))
    published["rag_docs_fingerprint"] = validation["docs_fingerprint"]
    published["rag_document_validation"] = {
        key: validation[key]
        for key in (
            "schema_version",
            "snapshot_id",
            "docs_fingerprint",
            "document_count",
            "source_counts",
            "card_documents_checked",
            "deck_documents_checked",
            "matchup_documents_checked",
            "passed",
            "failures",
        )
    }

    atomic_write(data_dir / "cards_meta.json", published["cards_meta"])
    atomic_write(data_dir / "top_decks.json", published["top_decks"])
    atomic_write(data_dir / "rag_documents.json", documents)
    archive_snapshot(data_dir, published, documents)
    atomic_write(data_dir / snapshot_file_name, published)
    atomic_write(
        data_dir / pointer_file_name,
        {
            "schema_version": 1,
            "snapshot_id": published["snapshot_id"],
            "published_at": published.get("published_at"),
        },
    )
    if not streamed:
        return published

    compact = {key: value for key, value in published.items() if not str(key).startswith("_")}
    compact["raw_battles"] = []
    compact["raw_battles_storage"] = {**compact["raw_battles_storage"], "loaded": False}
    work_dir = snapshot.get("_streaming_work_dir")
    if isinstance(work_dir, str):
        work_path = Path(work_dir).resolve()
        aggregate_path = Path(aggregate_source).resolve()
        expected_work_root = (data_dir / "snapshot_work").resolve()
        if (
            work_path.is_dir()
            and work_path.name.startswith("collection-")
            and work_path.parent == expected_work_root
            and aggregate_path.parent == work_path
        ):
            remove_tree(work_path)
    return compact
