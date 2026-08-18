"""Bounded answer lookup and durable, server-owned user feedback records."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RecentAnswerCache:
    def __init__(self, *, max_items: int = 512, ttl_seconds: float = 3600) -> None:
        self.max_items = max(1, int(max_items))
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, *, request_id: str, question: str, answer: str, **metadata: Any) -> None:
        record = {
            "request_id": str(request_id),
            "question": str(question),
            "answer": str(answer),
            **metadata,
        }
        with self._lock:
            self._purge_locked()
            self._items.pop(request_id, None)
            self._items[request_id] = (time.monotonic(), record)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(request_id)
            if item is None:
                return None
            self._items.move_to_end(request_id)
            return dict(item[1])

    def _purge_locked(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        expired = [key for key, (created, _) in self._items.items() if created < cutoff]
        for key in expired:
            self._items.pop(key, None)


class FeedbackStore:
    def __init__(self, path: Path, *, max_correction_chars: int = 4000, answer_ttl_seconds: int = 3600) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_correction_chars = max(1, int(max_correction_chars))
        self.answer_ttl_seconds = max(60, int(answer_ttl_seconds))
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    rating TEXT NOT NULL CHECK (rating IN ('positive', 'negative')),
                    correction TEXT,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    snapshot_id TEXT,
                    parsed_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recent_answers (
                    request_id TEXT PRIMARY KEY,
                    created_epoch REAL NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    snapshot_id TEXT,
                    parsed_json TEXT NOT NULL,
                    selected_skill TEXT
                )
                """
            )
            connection.commit()

    def register_answer(self, answer: dict[str, Any]) -> None:
        now = time.time()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("DELETE FROM recent_answers WHERE created_epoch < ?", (now - self.answer_ttl_seconds,))
            connection.execute(
                """
                INSERT OR REPLACE INTO recent_answers (
                    request_id, created_epoch, question, answer, snapshot_id,
                    parsed_json, selected_skill
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(answer["request_id"]), now, str(answer.get("question", "")),
                    str(answer.get("answer", "")), answer.get("snapshot_id"),
                    json.dumps(answer.get("parsed", {}), ensure_ascii=False, sort_keys=True),
                    answer.get("selected_skill"),
                ),
            )
            connection.commit()

    def get_answer(self, request_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT request_id, question, answer, snapshot_id, parsed_json, selected_skill
                FROM recent_answers WHERE request_id = ? AND created_epoch >= ?
                """,
                (request_id, time.time() - self.answer_ttl_seconds),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["parsed"] = json.loads(result.pop("parsed_json") or "{}")
        return result

    def submit(
        self,
        *,
        answer: dict[str, Any] | None,
        rating: str,
        correction: str | None = None,
    ) -> dict[str, Any]:
        if answer is None:
            raise LookupError("unknown or expired request_id")
        normalized_rating = str(rating).strip().lower()
        if normalized_rating not in {"positive", "negative"}:
            raise ValueError("rating must be positive or negative")
        normalized_correction = str(correction or "").strip() or None
        if normalized_correction and len(normalized_correction) > self.max_correction_chars:
            raise ValueError(f"correction exceeds {self.max_correction_chars} characters")
        record = {
            "feedback_id": f"fb-{uuid.uuid4().hex}",
            "request_id": str(answer["request_id"]),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rating": normalized_rating,
            "correction": normalized_correction,
            "question": str(answer.get("question", "")),
            "answer": str(answer.get("answer", "")),
            "snapshot_id": answer.get("snapshot_id"),
            "parsed_json": json.dumps(answer.get("parsed", {}), ensure_ascii=False, sort_keys=True),
        }
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO feedback (
                    feedback_id, request_id, created_at, rating, correction,
                    question, answer, snapshot_id, parsed_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(record[key] for key in (
                    "feedback_id", "request_id", "created_at", "rating", "correction",
                    "question", "answer", "snapshot_id", "parsed_json",
                )),
            )
            connection.commit()
        return {key: value for key, value in record.items() if key not in {"question", "answer", "parsed_json"}}

    def stats(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT rating, COUNT(*) AS count FROM feedback GROUP BY rating").fetchall()
        counts = {"positive": 0, "negative": 0}
        counts.update({str(row["rating"]): int(row["count"]) for row in rows})
        counts["total"] = counts["positive"] + counts["negative"]
        return counts

    def list_correction_candidates(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 10_000))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT feedback_id, request_id, created_at, question, answer,
                       correction, snapshot_id, parsed_json
                FROM feedback
                WHERE rating = 'negative' AND correction IS NOT NULL
                ORDER BY created_at ASC LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["parsed"] = json.loads(item.pop("parsed_json") or "{}")
            results.append(item)
        return results

