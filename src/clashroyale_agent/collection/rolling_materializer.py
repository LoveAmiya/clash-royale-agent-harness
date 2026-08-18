"""Materialize rolling dataset scopes as one atomic snapshot group."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from clashroyale_agent.collection.rolling_corpus import DATASET_SCOPES, RollingCorpusStore
from rag_document_policy import RAG_SOURCE_LIMITS, summarize_scope_documents
from structured_query import CARD_ALIAS_OVERRIDES, TOWER_DISPLAY_NAMES_ZH
from structured_stats import build_structured_stats


GROUP_SCHEMA_VERSION = 2
DELTA_SCOPE_PAIRS = (
    ("7d", "d7_14"),
    ("d7_14", "d14_21"),
    ("d14_21", "d21_28"),
    ("d21_28", "d28_35"),
)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _docs_fingerprint(documents: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(documents, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _generation_id(store: RollingCorpusStore, now: datetime) -> str:
    summaries = store.dataset_summaries(now=now)
    fingerprint = hashlib.sha256(
        json.dumps(summaries, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return f"pol-{now.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-{fingerprint}"


def _prune_group_versions(data_dir: Path, active_group_id: str, keep: int = 2) -> list[str]:
    groups_root = Path(data_dir) / "snapshot_groups"
    published: list[tuple[str, str, Path]] = []
    for directory in groups_root.iterdir() if groups_root.is_dir() else ():
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        group_id = str(manifest.get("snapshot_group_id") or "")
        if group_id != directory.name or manifest.get("fully_aligned") is not True:
            continue
        published.append((str(manifest.get("published_at") or ""), group_id, directory))
    published.sort(reverse=True)
    retained = {active_group_id}
    for _, group_id, _ in published:
        if len(retained) >= max(1, keep):
            break
        retained.add(group_id)
    removed = []
    for _, group_id, directory in published:
        if group_id in retained:
            continue
        try:
            shutil.rmtree(directory)
        except OSError:
            continue
        removed.append(group_id)
    return removed


def _write_scope_source(
    store: RollingCorpusStore,
    *,
    scope: str,
    now: datetime,
    data_dir: Path,
    snapshot_id: str,
) -> tuple[Path, dict]:
    archive = data_dir / "snapshot_archives" / snapshot_id
    archive.mkdir(parents=True, exist_ok=True)
    aggregate = archive / "aggregates.sqlite"
    connection = sqlite3.connect(aggregate)
    connection.execute(
        "CREATE TABLE battles(sequence INTEGER PRIMARY KEY AUTOINCREMENT, battle_id TEXT UNIQUE, payload TEXT NOT NULL)"
    )
    count = 0
    with connection:
        for record in store.iter_scope_battles(scope, now=now):
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
    return aggregate, summary


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _merge_scope_stats(target: sqlite3.Connection, source_path: Path, scope: str) -> None:
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        tables = [
            str(row[0])
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='metadata'"
            )
        ]
        for table in tables:
            columns = source.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
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
            placeholders = ",".join("?" for _ in range(len(column_names) + 1))
            selected = ",".join(_quoted(name) for name in column_names)
            cursor = source.execute(f"SELECT {selected} FROM {_quoted(table)}")
            while True:
                rows = cursor.fetchmany(1000)
                if not rows:
                    break
                target.executemany(
                    f"INSERT INTO {_quoted(table)} VALUES ({placeholders})",
                    ((scope, *tuple(row)) for row in rows),
                )
    finally:
        source.close()


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
    connection.execute(
        """
        CREATE TABLE meta_delta(
            current_scope TEXT NOT NULL,
            baseline_scope TEXT NOT NULL,
            category TEXT NOT NULL,
            item_id TEXT NOT NULL,
            current_sample INTEGER NOT NULL,
            baseline_sample INTEGER NOT NULL,
            current_usage_rate REAL NOT NULL,
            baseline_usage_rate REAL NOT NULL,
            usage_delta REAL NOT NULL,
            current_win_rate REAL NOT NULL,
            baseline_win_rate REAL NOT NULL,
            win_delta REAL NOT NULL,
            significant INTEGER NOT NULL,
            confidence_note TEXT NOT NULL,
            PRIMARY KEY(current_scope, baseline_scope, category, item_id)
        )
        """
    )

    def rows(table: str, scope: str, id_column: str, sample_column: str) -> dict[str, sqlite3.Row]:
        return {
            str(row[id_column]): row
            for row in connection.execute(
                f"SELECT * FROM {table} WHERE dataset_scope=?", (scope,)
            )
        }

    def insert_pair(
        current_scope: str,
        baseline_scope: str,
        category: str,
        current_rows: dict[str, sqlite3.Row],
        baseline_rows: dict[str, sqlite3.Row],
        sample_column: str,
        threshold: int,
    ) -> None:
        for item_id in sorted(set(current_rows) | set(baseline_rows)):
            current = current_rows.get(item_id)
            baseline = baseline_rows.get(item_id)
            current_sample = int(current[sample_column]) if current is not None else 0
            baseline_sample = int(baseline[sample_column]) if baseline is not None else 0
            current_usage = float(current["usage_rate"]) if current is not None else 0.0
            baseline_usage = float(baseline["usage_rate"]) if baseline is not None else 0.0
            current_win = float(current["clean_win_rate"]) if current is not None else 0.0
            baseline_win = float(baseline["clean_win_rate"]) if baseline is not None else 0.0
            current_interval = _wilson_interval(
                int(current["wins"]) if current is not None else 0,
                int(current["losses"]) if current is not None else 0,
            )
            baseline_interval = _wilson_interval(
                int(baseline["wins"]) if baseline is not None else 0,
                int(baseline["losses"]) if baseline is not None else 0,
            )
            intervals_separate = (
                current_interval[0] > baseline_interval[1]
                or baseline_interval[0] > current_interval[1]
            )
            enough = current_sample >= threshold and baseline_sample >= threshold
            appeared_or_disappeared = (
                (current_sample == 0 and baseline_sample >= threshold)
                or (baseline_sample == 0 and current_sample >= threshold)
            )
            meaningful_change = abs(current_usage - baseline_usage) >= 0.5 or abs(current_win - baseline_win) >= 3.0
            significant = appeared_or_disappeared or (enough and intervals_separate and meaningful_change)
            note = (
                "significant_wilson95_and_absolute_threshold"
                if significant else
                "observed_below_significance_threshold"
            )
            connection.execute(
                "INSERT INTO meta_delta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    current_scope,
                    baseline_scope,
                    category,
                    item_id,
                    current_sample,
                    baseline_sample,
                    current_usage,
                    baseline_usage,
                    round(current_usage - baseline_usage, 6),
                    current_win,
                    baseline_win,
                    round(current_win - baseline_win, 6),
                    int(significant),
                    note,
                ),
            )

    def full_loadout_base_rows(scope: str) -> dict[str, sqlite3.Row]:
        return {
            str(row["deck_signature"]): row
            for row in connection.execute(
                """
                SELECT base_deck_signature AS deck_signature,
                       SUM(games) AS games, SUM(wins) AS wins, SUM(losses) AS losses,
                       SUM(draws) AS draws, SUM(usage_rate) AS usage_rate,
                       CASE WHEN SUM(wins)+SUM(losses)=0 THEN 0
                            ELSE SUM(wins) * 100.0 / (SUM(wins)+SUM(losses)) END AS clean_win_rate
                FROM full_loadout_stats WHERE dataset_scope=?
                GROUP BY base_deck_signature
                """,
                (scope,),
            )
        }

    levels = ("top_100", "top_200", "top_500", "top_1000", "all")
    for current_prefix, baseline_prefix in DELTA_SCOPE_PAIRS:
        for level in levels:
            current_scope = f"{current_prefix}_{level}"
            baseline_scope = f"{baseline_prefix}_{level}"
            if not datasets[current_scope]["ready"] or not datasets[baseline_scope]["ready"]:
                continue
            insert_pair(
                current_scope,
                baseline_scope,
                "entity",
                rows("loadout_entity_stats", current_scope, "entity_id", "appearances"),
                rows("loadout_entity_stats", baseline_scope, "entity_id", "appearances"),
                "appearances",
                200,
            )
            insert_pair(
                current_scope,
                baseline_scope,
                "archetype",
                rows("archetype_stats", current_scope, "archetype", "games"),
                rows("archetype_stats", baseline_scope, "archetype", "games"),
                "games",
                200,
            )
            insert_pair(
                current_scope,
                baseline_scope,
                "deck",
                rows("deck_stats", current_scope, "deck_signature", "games"),
                rows("deck_stats", baseline_scope, "deck_signature", "games"),
                "games",
                30,
            )
            datasets[current_scope]["delta_ready"] = True
    for scope in DATASET_SCOPES:
        if not datasets[scope]["ready"] or not datasets[scope]["complete_loadout_ready"]:
            continue
        insert_pair(
            scope,
            scope,
            "base8_full_loadout_divergence",
            rows("deck_stats", scope, "deck_signature", "games"),
            full_loadout_base_rows(scope),
            "games",
            30,
        )
    connection.execute(
        "CREATE INDEX idx_meta_delta_scope ON meta_delta(current_scope, category, significant DESC)"
    )


def _rag_documents(connection: sqlite3.Connection, group_id: str, datasets: dict[str, dict]) -> list[dict]:
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


def _validate_documents(documents: list[dict], group_id: str) -> dict:
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
        "docs_fingerprint": _docs_fingerprint(documents),
    }


def build_snapshot_group(
    store: RollingCorpusStore,
    *,
    data_dir: Path,
    now: datetime | None = None,
    retriever_factory: Callable | None = None,
) -> dict:
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
        for scope in DATASET_SCOPES:
            snapshot_id = f"{group_id}--{scope}"
            scope_data = candidate / "scope_build" / scope
            _, summary = _write_scope_source(
                store,
                scope=scope,
                now=current,
                data_dir=scope_data,
                snapshot_id=snapshot_id,
            )
            structured_manifest = build_structured_stats(scope_data, snapshot_id)
            source_stats = scope_data / "structured_stats" / snapshot_id / "stats.sqlite"
            _merge_scope_stats(group_stats, source_stats, scope)
            dataset = {
                **store.dataset_summary(scope, now=current),
                "snapshot_id": snapshot_id,
                "structured_counts": structured_manifest["counts"],
                "complete_loadout_ready": (
                    structured_manifest["counts"].get("full_loadout_side_records", 0) > 0
                ),
                "entity_stats_ready": structured_manifest["counts"].get("loadout_entities", 0) > 0,
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
                    json.dumps(structured_manifest["counts"], ensure_ascii=False, sort_keys=True),
                ),
            )
            group_stats.commit()
            shutil.rmtree(scope_data, ignore_errors=True)
        _create_group_indexes(group_stats)
        _materialize_meta_deltas(group_stats, datasets)
        for scope, dataset in datasets.items():
            group_stats.execute(
                "UPDATE scope_metadata SET provenance_json=? WHERE dataset_scope=?",
                (json.dumps(dataset, ensure_ascii=False, sort_keys=True), scope),
            )
        group_stats.commit()
        documents = _rag_documents(group_stats, group_id, datasets)
        validation = _validate_documents(documents, group_id)
        if not validation["passed"]:
            raise ValueError("rolling RAG document validation failed")
        rag_scope_counts, rag_scope_source_counts = summarize_scope_documents(documents, DATASET_SCOPES)
        _atomic_json(candidate / "rag_documents.json", documents)
        group_stats.close()
        group_stats = None

        if retriever_factory is None:
            from hybrid_retriever import HybridRetriever

            retriever_factory = HybridRetriever
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

        stats_path = candidate / "structured_stats.sqlite"
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
            "fully_aligned": True,
            "cost_boundaries": {
                "cloud_llm_calls": 0,
                "cloud_embedding_calls": 0,
                "local_embedding_index_builds": 1,
            },
        }
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
        removed_groups = _prune_group_versions(data_dir, group_id, keep=2)
        manifest["removed_snapshot_group_ids"] = removed_groups
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

