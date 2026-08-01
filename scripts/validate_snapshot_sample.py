"""Validate a deterministic 30-battle sample without printing source records."""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 20260729


def _validate_battle(record: dict) -> list[str]:
    failures: list[str] = []
    for field in ("battle_id", "battle_time", "team_tag", "opponent_tag"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            failures.append(f"missing_{field}")
    for field in ("team_deck", "opponent_deck"):
        deck = record.get(field)
        if not isinstance(deck, list) or len(deck) != 8 or any(not isinstance(card, str) or not card for card in deck):
            failures.append(f"invalid_{field}")
    team_crowns = record.get("team_crowns")
    opponent_crowns = record.get("opponent_crowns")
    won = record.get("won")
    if not isinstance(team_crowns, int) or not isinstance(opponent_crowns, int) or not isinstance(won, bool):
        failures.append("invalid_result_fields")
    elif won != (team_crowns > opponent_crowns):
        failures.append("inconsistent_won")
    return failures


def validate_sample(data_dir: Path, snapshot_id: str, seed: int = DEFAULT_SEED) -> dict:
    database = data_dir / "snapshot_archives" / snapshot_id / "aggregates.sqlite"
    if not database.is_file():
        raise FileNotFoundError("snapshot aggregate database not found")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        total = int(connection.execute("SELECT COUNT(*) FROM battles").fetchone()[0])
        if total < 30:
            raise ValueError("snapshot has fewer than 30 battles")
        offsets = list(range(5)) + list(range(total - 5, total))
        offsets.extend(random.Random(seed).sample(range(5, total - 5), 20))
        records = []
        for offset in offsets:
            row = connection.execute(
                "SELECT payload FROM battles ORDER BY sequence LIMIT 1 OFFSET ?",
                (offset,),
            ).fetchone()
            if row is None:
                raise ValueError("sample offset is missing")
            records.append(json.loads(row[0]))
    finally:
        connection.close()

    failures = []
    battle_ids = []
    for sample_index, record in enumerate(records):
        battle_ids.append(record.get("battle_id"))
        failures.extend(
            {"sample_index": sample_index, "failure": failure}
            for failure in _validate_battle(record)
        )
    duplicate_ids = len(battle_ids) - len(set(battle_ids))
    if duplicate_ids:
        failures.append({"sample_index": None, "failure": "duplicate_battle_ids_in_sample"})
    failed_indices = {item["sample_index"] for item in failures if item["sample_index"] is not None}
    return {
        "status": "passed" if not failures else "failed",
        "snapshot_id": snapshot_id,
        "source_battles": total,
        "seed": seed,
        "sample_counts": {"first": 5, "last": 5, "random": 20, "total": 30},
        "passed_records": 30 - len(failed_indices),
        "duplicate_battle_ids": duplicate_ids,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    try:
        report = validate_sample(args.data_dir.resolve(), args.snapshot_id, args.seed)
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
