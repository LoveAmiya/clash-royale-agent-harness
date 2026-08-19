"""Batch validation and final-state persistence for the rolling corpus."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from clashroyale_agent.collection.corpus_normalization import as_utc, iso
from clashroyale_agent.collection.corpus_policy import BatchValidationPolicy, CorpusError


def finalize_batch(
    connection: sqlite3.Connection,
    batch_id: str,
    *,
    completed_at: datetime | str,
    policy: BatchValidationPolicy,
    request_count: int,
    rate_limited: int,
    refresh_budget_exhausted: bool,
    source_exhausted: bool,
    ranked_source: str,
    expansion_source: str,
    conflict_count: Callable[[str], int],
) -> dict:
    batch = connection.execute(
        "SELECT batch_type, status FROM collection_batches WHERE batch_id=?",
        (batch_id,),
    ).fetchone()
    if batch is None:
        raise CorpusError("unknown batch_id")
    top_successes = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM batch_players
            WHERE batch_id=? AND observer_source=? AND observer_rank<=? AND request_status='success'
            """,
            (batch_id, ranked_source, policy.required_top_rank),
        ).fetchone()[0]
    )
    ranked_successes = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM batch_players
            WHERE batch_id=? AND observer_source=? AND observer_rank<=? AND request_status='success'
            """,
            (batch_id, ranked_source, policy.ranked_player_target),
        ).fetchone()[0]
    )
    expansion_target = int(
        connection.execute(
            "SELECT COUNT(*) FROM batch_players WHERE batch_id=? AND observer_source=?",
            (batch_id, expansion_source),
        ).fetchone()[0]
    )
    expansion_successes = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM batch_players
            WHERE batch_id=? AND observer_source=? AND request_status='success'
            """,
            (batch_id, expansion_source),
        ).fetchone()[0]
    )
    unique_battles = int(
        connection.execute(
            "SELECT COUNT(DISTINCT battle_id) FROM battle_observations WHERE batch_id=?",
            (batch_id,),
        ).fetchone()[0]
    )
    coverage = ranked_successes / policy.ranked_player_target if policy.ranked_player_target else 0.0
    expansion_coverage = expansion_successes / expansion_target if expansion_target else 0.0
    failures = []
    if top_successes != policy.required_top_rank:
        failures.append("incomplete_top_rank_coverage")
    if coverage < policy.minimum_coverage:
        failures.append("ranked_coverage_below_threshold")
    if int(rate_limited) != 0:
        failures.append("rate_limited")
    if refresh_budget_exhausted:
        failures.append("refresh_budget_exhausted")
    if batch["status"] == "conflicted" or conflict_count(batch_id):
        failures.append("conflicting_battle_facts")
    if batch["batch_type"] == "weekly_expanded":
        if source_exhausted:
            if expansion_target == 0:
                failures.append("expansion_queue_empty")
            elif expansion_coverage < policy.minimum_expansion_coverage:
                failures.append("expansion_coverage_below_threshold")
            if unique_battles == 0:
                failures.append("no_usable_battles")
        elif unique_battles != policy.weekly_target_battles:
            failures.append("weekly_target_not_met")
    passed = not failures
    completed = as_utc(completed_at)
    report = {
        "passed": passed,
        "failures": failures,
        "top_rank_successes": top_successes,
        "top_rank_target": policy.required_top_rank,
        "ranked_successes": ranked_successes,
        "ranked_target": policy.ranked_player_target,
        "coverage": round(coverage, 6),
        "expansion_successes": expansion_successes,
        "expansion_target": expansion_target,
        "expansion_coverage": round(expansion_coverage, 6),
        "unique_battles": unique_battles,
    }
    with connection:
        connection.execute(
            """
            UPDATE collection_batches SET
                status=?, completed_at=?, expires_at=?, request_count=?, rate_limited=?,
                refresh_budget_exhausted=?, source_exhausted=?, ranked_successes=?, ranked_target=?,
                top_rank_successes=?, top_rank_target=?, coverage=?, unique_battles=?, validation_json=?
            WHERE batch_id=?
            """,
            (
                "accepted" if passed else "rejected",
                iso(completed),
                iso(completed + timedelta(days=35)),
                max(0, int(request_count)),
                max(0, int(rate_limited)),
                int(bool(refresh_budget_exhausted)),
                int(bool(source_exhausted)),
                ranked_successes,
                policy.ranked_player_target,
                top_successes,
                policy.required_top_rank,
                coverage,
                unique_battles,
                json.dumps(report, sort_keys=True, separators=(",", ":")),
                batch_id,
            ),
        )
        if not passed:
            connection.execute(
                "DELETE FROM battle_observations WHERE batch_id=?",
                (batch_id,),
            )
            connection.execute(
                """
                DELETE FROM battles
                WHERE NOT EXISTS (
                    SELECT 1 FROM battle_observations AS observations
                    WHERE observations.battle_id=battles.battle_id
                )
                """
            )
    return report
