"""Batch and publication-generation lifecycle writes for the rolling corpus."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from clashroyale_agent.collection.corpus_normalization import iso
from clashroyale_agent.collection.corpus_policy import CorpusError


def create_batch(
    connection: sqlite3.Connection,
    batch_id: str,
    *,
    batch_type: str,
    started_at: datetime | str,
    leaderboard_frozen_at: datetime | str,
    batch_types: set[str],
) -> None:
    normalized_id = str(batch_id or "").strip()
    if not normalized_id:
        raise CorpusError("batch_id is required")
    if batch_type not in batch_types:
        raise CorpusError("invalid batch_type")
    with connection:
        connection.execute(
            """
            INSERT INTO collection_batches(
                batch_id, batch_type, status, started_at, leaderboard_frozen_at
            ) VALUES (?, ?, 'collecting', ?, ?)
            """,
            (normalized_id, batch_type, iso(started_at), iso(leaderboard_frozen_at)),
        )


def batch_status(connection: sqlite3.Connection, batch_id: str) -> str | None:
    row = connection.execute(
        "SELECT status FROM collection_batches WHERE batch_id=?",
        (str(batch_id),),
    ).fetchone()
    return str(row[0]) if row is not None else None


def unique_batch_id(connection: sqlite3.Connection, preferred: str) -> str:
    candidate = str(preferred or "").strip()
    if not candidate:
        raise CorpusError("batch_id is required")
    if batch_status(connection, candidate) is None:
        return candidate
    attempt = 2
    while batch_status(connection, f"{candidate}-attempt-{attempt}") is not None:
        attempt += 1
    return f"{candidate}-attempt-{attempt}"


def begin_publication_generation(
    connection: sqlite3.Connection,
    generation_id: str,
    *,
    created_at: datetime | str,
) -> None:
    with connection:
        connection.execute(
            """
            INSERT INTO publication_generations(generation_id, status, created_at)
            VALUES (?, 'building', ?)
            ON CONFLICT(generation_id) DO UPDATE SET
                status='building', created_at=excluded.created_at, published_at=NULL, manifest_json='{}'
            """,
            (generation_id, iso(created_at)),
        )


def finish_publication_generation(
    connection: sqlite3.Connection,
    generation_id: str,
    *,
    status: str,
    manifest: dict,
    published_at: datetime | str | None = None,
) -> None:
    if status not in {"published", "failed"}:
        raise CorpusError("invalid publication generation status")
    with connection:
        connection.execute(
            """
            UPDATE publication_generations
            SET status=?, published_at=?, manifest_json=?
            WHERE generation_id=?
            """,
            (
                status,
                iso(published_at) if published_at is not None else None,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                generation_id,
            ),
        )
