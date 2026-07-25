# ADR-002: Aggregated RAG Evidence and Background Index Preheat

## Status

Accepted

## Context

The daily 20,000-battle snapshot is valuable for structured card, deck, and
matchup statistics, but a small overview/card/deck/matchup corpus does not give
open-ended RAG questions enough grounded evidence. Embedding every raw battle
would produce a large, repetitive corpus with poor retrieval precision. Lazy
index construction also moves Ollama embedding latency and failures into a user
request path.

## Decision

Raw battles remain in the canonical daily snapshot for audit and aggregation.
RAG indexes only derived, evidence-labelled documents: card profiles, exact-deck
profiles, heuristic archetype summaries, card-pair observations, and observed
card-versus-card counter evidence, in addition to the existing overview, card,
deck, and deck-matchup documents. Every document carries the same snapshot ID,
sample size, and source metadata.

After restoring a complete snapshot at startup, the backend starts RAG preheat
in the background. After publishing a new snapshot, it builds a new Qdrant index
before activating it. Qdrant storage is separated by snapshot ID and document
fingerprint, so the previous index remains intact while a replacement builds.
Requests only use an activated retriever that matches the active snapshot; they
never trigger document embedding.

## Consequences

- Structured metrics still come from deterministic aggregates, not RAG.
- `rag_status=ready` means dense plus BM25 retrieval is active.
- `rag_status=bm25_only` means the new snapshot has a safe lexical retriever but
  dense embedding was unavailable.
- `rag_status=building`, `not_ready`, or `failed` prevents unsupported open
  conclusions; the UI and trace expose the state.
- Archetypes are explicitly heuristic card-rule labels. The Supercell battle-log
  schema contains no card-play timeline, so opening and finish patterns are not
  invented from it.
