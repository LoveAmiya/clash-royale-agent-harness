"""Validation of complete snapshot-derived RAG evidence."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable


def validate_snapshot_rag_documents(
    snapshot: dict,
    documents: list[dict],
    *,
    is_complete: Callable[[object], bool],
    non_empty: Callable[[object], bool],
    rate_valid: Callable[[object], bool],
    missing_value: Callable[[dict], bool],
    build_documents: Callable[[dict], list[dict]],
    fingerprint: Callable[[list[dict]], str],
    min_matchup_games: int,
    min_aggregate_games: int,
) -> dict:
    snapshot_id = str(snapshot.get("snapshot_id") or "") if isinstance(snapshot, dict) else ""
    failures: set[str] = set()
    invalid_doc_ids: set[str] = set()
    source_counts: Counter[str] = Counter()
    seen_ids: set[str] = set()

    def invalidate(doc: object, failure: str) -> None:
        failures.add(failure)
        if isinstance(doc, dict):
            invalid_doc_ids.add(str(doc.get("doc_id") or "<missing-doc-id>"))

    if not is_complete(snapshot): failures.add("incomplete_snapshot")
    if not snapshot_id or not non_empty(snapshot.get("fetched_at")): failures.add("invalid_snapshot_identity")
    if not isinstance(snapshot.get("cards_meta"), list) or not snapshot.get("cards_meta"): failures.add("cards_meta_missing")
    if not isinstance(snapshot.get("top_decks"), list) or not snapshot.get("top_decks"): failures.add("top_decks_missing")
    if not isinstance(documents, list) or not documents:
        failures.add("rag_documents_missing"); documents = []
    for doc in documents:
        if not isinstance(doc, dict): invalidate(doc, "invalid_evidence_fields"); continue
        doc_id, source_type, metadata = doc.get("doc_id"), doc.get("source_type"), doc.get("metadata")
        if not non_empty(doc_id) or doc_id in seen_ids: invalidate(doc, "duplicate_or_missing_doc_id")
        else: seen_ids.add(doc_id)
        if not non_empty(source_type) or not non_empty(doc.get("text")): invalidate(doc, "invalid_evidence_fields")
        else: source_counts[source_type] += 1
        if not isinstance(metadata, dict): invalidate(doc, "invalid_evidence_fields"); continue
        if any(not non_empty(metadata.get(key)) for key in ("snapshot_id", "fetched_at", "source")): invalidate(doc, "invalid_evidence_fields")
        if metadata.get("snapshot_id") != snapshot_id: invalidate(doc, "snapshot_id_mismatch")
        if metadata.get("fetched_at") != snapshot.get("fetched_at") or metadata.get("sample_battles") != snapshot.get("sample_battles"): invalidate(doc, "snapshot_metadata_mismatch")
        if metadata.get("source") != "Supercell API live sample": invalidate(doc, "source_mismatch")
        if missing_value(doc): invalidate(doc, "invalid_evidence_fields")
    try:
        expected_documents = build_documents(snapshot)
    except (KeyError, TypeError, ValueError):
        expected_documents = []; failures.add("rag_document_build_failed")
    expected_by_id = {doc.get("doc_id"): doc for doc in expected_documents if isinstance(doc, dict) and non_empty(doc.get("doc_id"))}
    actual_by_id = {doc.get("doc_id"): doc for doc in documents if isinstance(doc, dict) and non_empty(doc.get("doc_id"))}
    if set(expected_by_id) != set(actual_by_id):
        failures.add("rag_document_coverage_mismatch"); invalid_doc_ids.update(str(value) for value in set(expected_by_id) ^ set(actual_by_id))
    for doc_id in set(expected_by_id) & set(actual_by_id):
        if expected_by_id[doc_id] != actual_by_id[doc_id]:
            kind = expected_by_id[doc_id].get("source_type")
            invalidate(actual_by_id[doc_id], {"card": "card_document_mismatch", "deck": "deck_document_mismatch", "matchup": "matchup_document_mismatch"}.get(kind, "aggregate_document_mismatch"))
    for doc in documents:
        if not isinstance(doc, dict) or not isinstance(doc.get("metadata"), dict): continue
        metadata, source_type = doc["metadata"], doc.get("source_type")
        valid = True
        if source_type == "card": valid = non_empty(metadata.get("card_name")) and isinstance(metadata.get("rank"), int) and metadata["rank"] > 0 and rate_valid(metadata.get("usage_rate")) and rate_valid(metadata.get("win_rate")) and rate_valid(metadata.get("clean_win_rate")) and isinstance(metadata.get("appearance_count"), int) and metadata["appearance_count"] >= 0
        elif source_type == "deck":
            cards = metadata.get("cards"); valid = non_empty(metadata.get("deck_name")) and isinstance(metadata.get("rank"), int) and metadata["rank"] > 0 and isinstance(cards, list) and len(cards) == 8 and len(set(cards)) == 8 and all(non_empty(card) for card in cards) and isinstance(metadata.get("battles"), int) and metadata["battles"] > 0 and rate_valid(metadata.get("sample_win_rate"))
        elif source_type == "matchup": valid = non_empty(metadata.get("deck_name")) and non_empty(metadata.get("opponent_deck_name")) and isinstance(metadata.get("games"), int) and metadata["games"] >= min_matchup_games and isinstance(metadata.get("wins"), int) and 0 <= metadata["wins"] <= metadata["games"] and rate_valid(metadata.get("win_rate"))
        elif source_type == "card_profile": valid = non_empty(metadata.get("card_name")) and isinstance(metadata.get("games"), int) and metadata["games"] > 0 and rate_valid(metadata.get("win_rate"))
        elif source_type in {"deck_profile", "card_pair", "counter"}: valid = isinstance(metadata.get("games"), int) and metadata["games"] >= min_aggregate_games
        elif source_type == "archetype": valid = non_empty(metadata.get("archetype")) and isinstance(metadata.get("games"), int) and metadata["games"] > 0 and rate_valid(metadata.get("win_rate"))
        elif source_type != "snapshot": valid = False
        if not valid: invalidate(doc, "unknown_source_type" if source_type not in {"snapshot", "card", "deck", "matchup", "card_profile", "deck_profile", "archetype", "card_pair", "counter"} else "invalid_evidence_fields")
    return {"schema_version": 1, "snapshot_id": snapshot_id or None, "docs_fingerprint": fingerprint(documents), "document_count": len(documents), "source_counts": dict(source_counts), "card_documents_checked": source_counts.get("card", 0), "deck_documents_checked": source_counts.get("deck", 0), "matchup_documents_checked": source_counts.get("matchup", 0), "passed": not failures, "failures": sorted(failures), "invalid_doc_ids": sorted(invalid_doc_ids)}
