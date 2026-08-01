"""Read a collector workspace's SQLite counters without printing source records."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _latest_collection(workspace_root: Path) -> Path:
    candidates = [path for path in workspace_root.glob("collection-*") if path.is_dir()]
    if not candidates:
        raise FileNotFoundError("no collection workspace found")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _count_rows(connection: sqlite3.Connection, table: str) -> int:
    escaped = table.replace('"', '""')
    return int(connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0])


def read_workspace_status(workspace_root: Path) -> dict:
    collection = _latest_collection(workspace_root)
    database = collection / "aggregates.sqlite"
    if not database.is_file():
        raise FileNotFoundError("collector SQLite database not found")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=5)
    try:
        table_names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if isinstance(row[0], str) and not str(row[0]).startswith("sqlite_")
        }
        counts = {
            table: _count_rows(connection, table)
            for table in sorted(table_names)
            if table in {"battles", "processed_players", "card_stats", "deck_stats", "matchup_stats"}
        }
        metadata = {}
        if "metadata" in table_names:
            allowed_keys = {
                "target_battles",
                "player_limit",
                "battles_per_player",
                "seed_player_limit",
                "scope_contract",
                "failed_players",
                "sampled_players",
                "rate_limited",
            }
            metadata = {
                str(key): value
                for key, value in connection.execute("SELECT key, value FROM metadata")
                if str(key) in allowed_keys
            }
    finally:
        connection.close()
    return {
        "status": "collecting",
        "workspace": collection.name,
        "database_bytes": database.stat().st_size,
        "counts": counts,
        "metadata": metadata,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=ROOT / "data" / "snapshot_work")
    args = parser.parse_args()
    try:
        result = read_workspace_status(args.workspace_root.resolve())
    except (OSError, sqlite3.Error) as exc:
        print(json.dumps({"status": "unavailable", "error": type(exc).__name__}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
