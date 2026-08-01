"""Export active environment archetypes and classifier evidence for human review."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from answer_presentation import card_display_names
from deck_archetypes import ARCHETYPE_CATALOG, archetype_definition, archetype_family, classify_deck


_PRIORITY = {item.name: index for index, item in enumerate(ARCHETYPE_CATALOG, start=1)}


def _metadata_value(raw: str) -> object:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


def _rate(wins: int, losses: int) -> float:
    decisions = wins + losses
    return round(wins / decisions * 100, 4) if decisions else 0.0


def _translated(cards: list[str] | tuple[str, ...], display_names: dict[str, str]) -> list[str]:
    return [display_names.get(card, card) for card in cards]


def _rule_payload(archetype: str, display_names: dict[str, str]) -> dict:
    rule = archetype_definition(archetype)
    if rule is None:
        return {
            "priority": _PRIORITY.get(archetype),
            "family": archetype_family(archetype),
            "description": "该类别用于承接无清晰核心、多核心冲突、样本噪声或尚未覆盖的新卡组。",
            "anchor_card_ids": [],
            "anchor_cards_zh": [],
            "required_all_card_ids": [],
            "required_any_card_ids": [],
            "support_weights": [],
            "blocker_weights": [],
            "feature_weights": [],
        }
    return {
        "priority": _PRIORITY[archetype],
        "family": rule.family,
        "description": rule.description,
        "anchor_card_ids": list(rule.anchors),
        "anchor_cards_zh": _translated(rule.anchors, display_names),
        "required_all_card_ids": list(rule.required_all),
        "required_all_cards_zh": _translated(rule.required_all, display_names),
        "required_any_card_ids": list(rule.required_any),
        "required_any_cards_zh": _translated(rule.required_any, display_names),
        "support_weights": [
            {
                "card_id": card,
                "display_name_zh": display_names.get(card, card),
                "weight": weight,
            }
            for card, weight in rule.supports
        ],
        "blocker_weights": [
            {
                "card_id": card,
                "display_name_zh": display_names.get(card, card),
                "penalty": penalty,
            }
            for card, penalty in rule.blockers
        ],
        "feature_weights": [
            {"feature": feature, "weight_per_card": weight}
            for feature, weight in rule.feature_weights
        ],
        "minimum_score": rule.min_score,
    }


def export_archetype_naming_package(
    database_path: Path,
    output_path: Path,
    representative_limit: int = 12,
    core_card_limit: int = 16,
) -> dict:
    display_names = card_display_names()
    connection = sqlite3.connect(f"file:{database_path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        metadata = {
            row["key"]: _metadata_value(row["value"])
            for row in connection.execute("SELECT key, value FROM metadata")
        }
        archetype_rows = list(
            connection.execute("SELECT * FROM archetype_stats ORDER BY games DESC, archetype")
        )
        exported = []
        for row in archetype_rows:
            archetype = str(row["archetype"])
            deck_rows = list(
                connection.execute(
                    """
                    SELECT ad.games, ad.wins, ad.losses, ad.draws, ds.deck_json
                    FROM archetype_decks AS ad
                    JOIN deck_stats AS ds ON ds.deck_signature = ad.deck_signature
                    WHERE ad.archetype = ?
                    ORDER BY ad.games DESC, ad.deck_signature
                    """,
                    (archetype,),
                )
            )
            card_games: dict[str, int] = defaultdict(int)
            representatives = []
            for deck_index, deck_row in enumerate(deck_rows):
                card_ids = json.loads(deck_row["deck_json"])
                cards_zh = _translated(card_ids, display_names)
                games = int(deck_row["games"])
                for card in card_ids:
                    card_games[card] += games
                if deck_index < representative_limit:
                    classification = classify_deck(tuple(card_ids))
                    representatives.append(
                        {
                            "games": games,
                            "wins": int(deck_row["wins"]),
                            "losses": int(deck_row["losses"]),
                            "draws": int(deck_row["draws"]),
                            "clean_win_rate": _rate(int(deck_row["wins"]), int(deck_row["losses"])),
                            "card_ids": card_ids,
                            "cards_zh": cards_zh,
                            "classification_confidence": classification.confidence,
                            "classification_score": classification.score,
                            "matched_signals": list(classification.matched_signals),
                            "classification_reason": classification.reason,
                        }
                    )
            games = int(row["games"])
            core_cards = [
                {
                    "card_id": card,
                    "display_name_zh": display_names.get(card, card),
                    "deck_games": count,
                    "archetype_deck_share": round(count / games * 100, 4) if games else 0.0,
                }
                for card, count in sorted(
                    card_games.items(),
                    key=lambda item: (-item[1], display_names.get(item[0], item[0])),
                )[:core_card_limit]
            ]
            exported.append(
                {
                    "current_name": archetype,
                    "family": archetype_family(archetype),
                    "reviewed_name": "",
                    "review_notes": "",
                    "classification_rule": _rule_payload(archetype, display_names),
                    "statistics": {
                        "games": games,
                        "wins": int(row["wins"]),
                        "losses": int(row["losses"]),
                        "draws": int(row["draws"]),
                        "usage_rate": float(row["usage_rate"]),
                        "clean_win_rate": float(row["clean_win_rate"]),
                        "net_win_rate": float(row["net_win_rate"]),
                    },
                    "distinct_decks": len(deck_rows),
                    "representative_deck_coverage": round(
                        sum(item["games"] for item in representatives) / games * 100,
                        4,
                    ) if games else 0.0,
                    "core_cards": core_cards,
                    "representative_decks": representatives,
                }
            )
    finally:
        connection.close()

    payload = {
        "schema_version": 2,
        "snapshot_id": metadata.get("snapshot_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "structured Path of Legend snapshot statistics",
        "instructions": (
            "人工填写 reviewed_name 和 review_notes。分类器按胜利条件锚点与配件特征加权，"
            "代表卡组中的 matched_signals 和 classification_reason 用于核查误分；"
            "其他卡组应优先检查是否需要新增规则，不要求为每套发明家卡组单独命名。"
        ),
        "archetype_count": len(exported),
        "catalog_count": len(ARCHETYPE_CATALOG),
        "archetypes": exported,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload


def _active_snapshot_id(data_dir: Path) -> str:
    pointer = json.loads((data_dir / "official_snapshot_pointer.json").read_text(encoding="utf-8"))
    snapshot_id = str(pointer.get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise ValueError("active snapshot pointer has no snapshot_id")
    return snapshot_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--representative-limit", type=int, default=12)
    args = parser.parse_args()
    snapshot_id = args.snapshot_id or _active_snapshot_id(args.data_dir)
    database_path = args.data_dir / "structured_stats" / snapshot_id / "stats.sqlite"
    output_path = args.data_dir / "manual_review" / f"archetype_naming_{snapshot_id}.json"
    payload = export_archetype_naming_package(
        database_path,
        output_path,
        representative_limit=max(1, args.representative_limit),
    )
    print(
        json.dumps(
            {
                "status": "ready",
                "snapshot_id": payload["snapshot_id"],
                "archetypes": payload["archetype_count"],
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
