# ADR-001: Daily Complete Official Snapshot

## Status

Accepted

## Context

The official Clash Royale API exposes bounded player battle logs rather than a
global real-time meta endpoint. Refreshing user-selectable samples every few
minutes created unnecessary API pressure, rate-limit risk, and inconsistent
evidence between structured answers and RAG answers. Repository JSON and static
strategy documents also could not be represented as current environment data.

## Decision

Production uses one fixed 20,000-battle official snapshot at most once every 24
hours. Only a collection with exactly 20,000 unique usable battles, no shortfall,
no refresh-budget exhaustion, and no rate-limit event may replace the previous
published snapshot.

The collector starts at global leaderboard rank 1 and follows the official
ranking cursor in returned order through at most 3,000 candidates. It stops
immediately when the 20,000-battle target is reached. Battle deduplication uses
the battle timestamp plus both player tags, both decks, and crown counts with
the two sides normalized; timestamp-only matching is not used. Records without
a timestamp remain usable but are not globally deduplicated because identical
decks or players alone do not prove a duplicate battle.

The canonical file is `data/official_daily_snapshot.json`. Its derived card,
deck, matchup, and RAG JSON files share a `snapshot_id`. Qdrant local storage
persists a vector collection and a manifest containing that ID plus a document
fingerprint. Restarting reuses the index when both match; a new complete snapshot
regenerates the RAG documents and index.

## Consequences

- Data and RAG answers cite one coherent daily evidence boundary.
- A restart can serve the last complete official snapshot immediately.
- The first run without a snapshot waits for collection and indexing.
- Partial official data remains observable in logs but is never published.
- The UI exposes source provenance, candidate pool, scanned rank range, sample
  size, collection time, and duplicate counts without initiating a refresh.
- Exact card and deck metrics read structured aggregates; RAG is used for
  evidence-constrained explanation, not numerical calculation.
