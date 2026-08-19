"""RAG document assembly and validation for rolling snapshot groups."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter

from clashroyale_agent.collection.materializer_primitives import docs_fingerprint
from clashroyale_agent.collection.rolling_corpus import DATASET_SCOPES
from rag_document_policy import RAG_SOURCE_LIMITS
from structured_query import CARD_ALIAS_OVERRIDES, TOWER_DISPLAY_NAMES_ZH


def build_rag_documents(connection: sqlite3.Connection, group_id: str, datasets: dict[str, dict]) -> list[dict]:
    connection.row_factory = sqlite3.Row
    documents: list[dict] = []
    for scope in DATASET_SCOPES:
        dataset = datasets[scope]
        common = {
            "snapshot_id": group_id,
            "snapshot_group_id": group_id,
            "scope_snapshot_id": dataset["snapshot_id"],
            "dataset_scope": scope,
            "window_started_at": dataset["window_started_at"],
            "window_ended_at": dataset["window_ended_at"],
            "sample_battles": dataset["unique_battles"],
            "unique_battles": dataset["unique_battles"],
            "weekly_batch_count": dataset["weekly_batch_count"],
            "daily_batch_count": dataset["daily_batch_count"],
            "ranked_coverage": dataset["ranked_coverage"],
            "missing_collection_dates": dataset["missing_collection_dates"],
            "source": "Supercell API rolling Path of Legend corpus",
            "full_loadout_battles": dataset["structured_counts"].get("full_loadout_battles", 0),
            "full_loadout_side_records": dataset["structured_counts"].get("full_loadout_side_records", 0),
            "excluded_incomplete_loadouts": dataset["structured_counts"].get("excluded_incomplete_loadouts", 0),
        }
        documents.append(
            {
                "doc_id": f"{group_id}:{scope}:overview",
                "source_type": "snapshot",
                "text": (
                    f"Path of Legend rolling dataset {scope} contains {dataset['unique_battles']} unique battles "
                    f"from {dataset['window_started_at']} through {dataset['window_ended_at']}."
                ),
                "metadata": common,
            }
        )
        for rank, row in enumerate(
            connection.execute(
                "SELECT * FROM card_stats WHERE dataset_scope=? ORDER BY usage_rate DESC, card_name",
                (scope,),
            ),
            start=1,
        ):
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:card:{row['card_name']}",
                    "source_type": "card",
                    "text": (
                        f"Card evidence for {row['card_name']}: rank {rank}, usage {row['usage_rate']}%, "
                        f"clean win rate {row['clean_win_rate']}%, {row['appearances']} appearances."
                    ),
                    "metadata": {
                        **common,
                        "card_name": row["card_name"],
                        "rank": rank,
                        "usage_rate": row["usage_rate"],
                        "win_rate": row["clean_win_rate"],
                        "clean_win_rate": row["clean_win_rate"],
                        "appearance_count": row["appearances"],
                    },
                }
            )
        for rank, row in enumerate(
            connection.execute(
                "SELECT * FROM loadout_entity_stats WHERE dataset_scope=? "
                "ORDER BY usage_rate DESC, entity_id",
                (scope,),
            ),
            start=1,
        ):
            if row["entity_type"] == "tower":
                entity_payload = json.loads(row["entity_json"])
                source_name = str(entity_payload.get("name") or row["tower_id"])
                display_name = TOWER_DISPLAY_NAMES_ZH.get(source_name, source_name)
            else:
                source_name = str(row["card_name"] or row["card_id"])
                base_name = CARD_ALIAS_OVERRIDES.get(source_name, [source_name])[0]
                display_name = (
                    f"觉醒{base_name}" if row["special_state"] == "evolution" else
                    f"精英{base_name}" if row["special_state"] == "elite" else
                    base_name
                )
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:entity:{row['entity_id']}",
                    "source_type": "card_variant" if row["entity_type"] == "card" else "tower",
                    "text": (
                        f"完整配置实体证据：{display_name}，排名 {rank}，使用率 {row['usage_rate']}%，"
                        f"胜率 {row['clean_win_rate']}%，样本 {row['appearances']} 次。"
                    ),
                    "metadata": {
                        **common,
                        "deck_mode": "full_loadout",
                        "entity_mode": "loadout_entity",
                        "entity_id": row["entity_id"],
                        "entity_type": row["entity_type"],
                        "special_state": row["special_state"],
                        "display_name_zh": display_name,
                        "rank": rank,
                        "usage_rate": row["usage_rate"],
                        "win_rate": row["clean_win_rate"],
                        "appearance_count": row["appearances"],
                    },
                }
            )
        deck_rows = connection.execute(
            f"SELECT * FROM deck_stats WHERE dataset_scope=? ORDER BY games DESC, deck_signature LIMIT {RAG_SOURCE_LIMITS['deck']}",
            (scope,),
        ).fetchall()
        for rank, row in enumerate(deck_rows, start=1):
            cards = json.loads(row["deck_json"])
            deck_name = " / ".join(cards)
            metadata = {
                **common,
                "deck_mode": "base8",
                "deck_name": deck_name,
                "rank": rank,
                "cards": cards,
                "games": row["games"],
                "sample_win_rate": row["clean_win_rate"],
            }
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:deck:{row['deck_signature']}",
                    "source_type": "deck",
                    "text": f"Deck evidence: {deck_name}; {row['games']} games, {row['clean_win_rate']}% win rate.",
                    "metadata": metadata,
                }
            )
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:deck-profile:{row['deck_signature']}",
                    "source_type": "deck_profile",
                    "text": f"Deck profile: {deck_name}; observed {row['games']} times in {scope}.",
                    "metadata": metadata,
                }
            )
        for row in connection.execute(
            "SELECT * FROM archetype_stats WHERE dataset_scope=? ORDER BY games DESC, archetype",
            (scope,),
        ):
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:archetype:{row['archetype']}",
                    "source_type": "archetype",
                    "text": (
                        f"Archetype evidence for {row['archetype']}: {row['games']} side records, "
                        f"usage {row['usage_rate']}%, win rate {row['clean_win_rate']}%."
                    ),
                    "metadata": {
                        **common,
                        "deck_mode": "base8",
                        "archetype": row["archetype"],
                        "games": row["games"],
                        "usage_rate": row["usage_rate"],
                        "win_rate": row["clean_win_rate"],
                        "classification": row["classification"],
                    },
                }
            )
        delta_rows = connection.execute(
            "SELECT * FROM meta_delta WHERE current_scope=? "
            "ORDER BY significant DESC, ABS(usage_delta) DESC, ABS(win_delta) DESC "
            f"LIMIT {RAG_SOURCE_LIMITS['meta_delta'] - 1}",
            (scope,),
        ).fetchall()
        if delta_rows:
            significant_count = sum(int(row["significant"]) for row in delta_rows)
            baseline_scope = str(delta_rows[0]["baseline_scope"])
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:meta-delta:overview",
                    "source_type": "meta_delta",
                    "text": (
                        f"环境变化证据：{scope} 与 {baseline_scope} 比较，共物化 "
                        f"{len(delta_rows)} 项变化，其中 {significant_count} 项达到显著阈值。"
                    ),
                    "metadata": {
                        **common,
                        "baseline_scope": baseline_scope,
                        "delta_count": len(delta_rows),
                        "significant_count": significant_count,
                    },
                }
            )
            for row in delta_rows:
                item_hash = hashlib.sha256(str(row["item_id"]).encode("utf-8")).hexdigest()[:16]
                documents.append(
                    {
                        "doc_id": f"{group_id}:{scope}:meta-delta:{row['category']}:{item_hash}",
                        "source_type": "meta_delta",
                        "text": (
                            f"{row['category']} {row['item_id']}：使用率变化 {row['usage_delta']} 个百分点，"
                            f"胜率变化 {row['win_delta']} 个百分点；当前样本 {row['current_sample']}，"
                            f"对照样本 {row['baseline_sample']}，"
                            f"{'达到显著阈值' if row['significant'] else '仅为观察结果'}。"
                        ),
                        "metadata": {
                            **common,
                            "baseline_scope": row["baseline_scope"],
                            "delta_category": row["category"],
                            "item_id": row["item_id"],
                            "usage_delta": row["usage_delta"],
                            "win_delta": row["win_delta"],
                            "current_sample": row["current_sample"],
                            "baseline_sample": row["baseline_sample"],
                            "significant": bool(row["significant"]),
                            "confidence_note": row["confidence_note"],
                        },
                    }
                )
        for rank, row in enumerate(
            connection.execute(
                "SELECT * FROM full_loadout_stats WHERE dataset_scope=? ORDER BY games DESC, loadout_signature "
                f"LIMIT {RAG_SOURCE_LIMITS['full_loadout']}",
                (scope,),
            ),
            start=1,
        ):
            loadout = json.loads(row["loadout_json"])
            tower_name = (loadout.get("tower") or {}).get("name") or (loadout.get("tower") or {}).get("id")
            cards = [card.get("name") or card.get("id") for card in loadout.get("cards", [])]
            evolved = [card for card in loadout.get("cards", []) if int(card.get("evolution_level") or 0) == 1]
            elite = [card for card in loadout.get("cards", []) if card.get("elite") is True]
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:full-loadout:{row['loadout_signature']}",
                    "source_type": "full_loadout",
                    "text": (
                        f"Complete loadout evidence: tower {tower_name}; cards {' / '.join(cards)}; "
                        f"{len(evolved)} evolved and {len(elite)} elite cards; "
                        f"{row['games']} games, {row['clean_win_rate']}% win rate."
                    ),
                    "metadata": {
                        **common,
                        "deck_mode": "full_loadout",
                        "rank": rank,
                        "loadout_signature": row["loadout_signature"],
                        "tower": loadout.get("tower"),
                        "cards": loadout.get("cards", []),
                        "games": row["games"],
                        "win_rate": row["clean_win_rate"],
                    },
                }
            )
        for row in connection.execute(
            f"""
            SELECT * FROM full_loadout_matchup_stats WHERE dataset_scope=?
            ORDER BY games DESC, loadout_a_signature, loadout_b_signature
            LIMIT {RAG_SOURCE_LIMITS['full_loadout_matchup']}
            """,
            (scope,),
        ):
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:full-matchup:{row['loadout_a_signature']}::{row['loadout_b_signature']}",
                    "source_type": "full_loadout_matchup",
                    "text": (
                        f"Exact complete-loadout matchup: {row['games']} games; "
                        f"first configuration won {row['wins_a']} and second won {row['wins_b']}."
                    ),
                    "metadata": {
                        **common,
                        "deck_mode": "full_loadout",
                        "loadout_a_signature": row["loadout_a_signature"],
                        "loadout_b_signature": row["loadout_b_signature"],
                        "games": row["games"],
                        "wins": row["wins_a"],
                        "win_rate": round(row["wins_a"] / max(1, row["wins_a"] + row["wins_b"]) * 100, 6),
                    },
                }
            )
        for row in connection.execute(
            f"""
            SELECT * FROM matchup_stats WHERE dataset_scope=?
            ORDER BY games DESC, deck_a_signature, deck_b_signature
            LIMIT {RAG_SOURCE_LIMITS['matchup']}
            """,
            (scope,),
        ):
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:matchup:{row['deck_a_signature']}::{row['deck_b_signature']}",
                    "source_type": "matchup",
                    "text": (
                        f"Exact deck matchup evidence: {row['deck_a_signature']} versus {row['deck_b_signature']}; "
                        f"{row['games']} games, first deck won {row['wins_a']} times."
                    ),
                    "metadata": {
                        **common,
                        "deck_name": row["deck_a_signature"],
                        "opponent_deck_name": row["deck_b_signature"],
                        "games": row["games"],
                        "wins": row["wins_a"],
                        "win_rate": round(row["wins_a"] / max(1, row["wins_a"] + row["wins_b"]) * 100, 6),
                    },
                }
            )
        for row in connection.execute(
            f"""
            SELECT card_name, teammate_name, games, wins, losses FROM card_teammates
            WHERE dataset_scope=? AND card_name<teammate_name
            ORDER BY games DESC, card_name, teammate_name
            LIMIT {RAG_SOURCE_LIMITS['card_pair']}
            """,
            (scope,),
        ):
            decisions = row["wins"] + row["losses"]
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:card-pair:{row['card_name']}::{row['teammate_name']}",
                    "source_type": "card_pair",
                    "text": f"Card pair {row['card_name']} and {row['teammate_name']} appeared in {row['games']} side records.",
                    "metadata": {
                        **common,
                        "cards": [row["card_name"], row["teammate_name"]],
                        "games": row["games"],
                        "sample_win_rate": round(row["wins"] / decisions * 100, 6) if decisions else 0.0,
                    },
                }
            )
        for row in connection.execute(
            f"""
            SELECT card_name, opponent_name, games, wins, losses FROM card_opponents
            WHERE dataset_scope=? ORDER BY games DESC, card_name, opponent_name
            LIMIT {RAG_SOURCE_LIMITS['counter']}
            """,
            (scope,),
        ):
            decisions = row["wins"] + row["losses"]
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:counter:{row['card_name']}::{row['opponent_name']}",
                    "source_type": "counter",
                    "text": f"Observed matchup evidence for {row['card_name']} against {row['opponent_name']} in {row['games']} side records.",
                    "metadata": {
                        **common,
                        "card_name": row["card_name"],
                        "opponent_card_name": row["opponent_name"],
                        "games": row["games"],
                        "win_rate": round(row["wins"] / decisions * 100, 6) if decisions else 0.0,
                    },
                }
            )
        for row in connection.execute(
            "SELECT * FROM card_stats WHERE dataset_scope=? ORDER BY appearances DESC, card_name "
            f"LIMIT {RAG_SOURCE_LIMITS['card_profile']}",
            (scope,),
        ):
            documents.append(
                {
                    "doc_id": f"{group_id}:{scope}:card-profile:{row['card_name']}",
                    "source_type": "card_profile",
                    "text": f"Card profile for {row['card_name']}: {row['appearances']} appearances and {row['clean_win_rate']}% win rate.",
                    "metadata": {
                        **common,
                        "card_name": row["card_name"],
                        "games": row["appearances"],
                        "win_rate": row["clean_win_rate"],
                    },
                }
            )
    return documents


def validate_documents(documents: list[dict], group_id: str) -> dict:
    failures = []
    doc_ids = [str(doc.get("doc_id") or "") for doc in documents]
    scopes = {doc.get("metadata", {}).get("dataset_scope") for doc in documents}
    if not documents or not all(doc_ids) or len(doc_ids) != len(set(doc_ids)):
        failures.append("invalid_or_duplicate_doc_ids")
    if scopes != set(DATASET_SCOPES):
        failures.append("dataset_scope_coverage_mismatch")
    if any(doc.get("metadata", {}).get("snapshot_group_id") != group_id for doc in documents):
        failures.append("snapshot_group_mismatch")
    return {
        "passed": not failures,
        "failures": failures,
        "document_count": len(documents),
        "source_counts": dict(Counter(str(doc.get("source_type")) for doc in documents)),
        "docs_fingerprint": docs_fingerprint(documents),
    }

