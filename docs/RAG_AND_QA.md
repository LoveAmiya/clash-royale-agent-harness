# RAG and QA Guide

## Public API Examples

Structured ranking endpoints answer from local facts and do not call the model:

~~~text
GET /api/cards/rankings?dataset_scope=7d_all&sort_by=usage_rate
GET /api/entities/rankings?dataset_scope=7d_all&sort_by=usage_rate
GET /api/entities/catalog?dataset_scope=7d_all
GET /api/entities/card%3A26000000%3Aevolution/stats?dataset_scope=7d_all
POST /api/entities/compare
~~~

sort_by accepts usage_rate, clean_win_rate, or rating. Entity IDs are stable: card:{official_card_id}:ordinary, card:{official_card_id}:evolution, card:{official_card_id}:elite, and tower:{official_tower_id}. A scope without complete-loadout entity statistics returns ENTITY_STATS_NOT_READY and must not fall back to base-eight statistics.

The main natural-language QA endpoint is:

~~~powershell
curl -X POST http://127.0.0.1:8091/process ^
  -H "Content-Type: application/json" ^
  -d "{\"query\":\"当前环境以哪些体系为主？\"}"
~~~

This document describes how questions move through parsing, structured facts, RAG retrieval, evidence validation and browser streaming. It is a contract for behavior, not a prompt dump.

## Implementation Boundaries

`src/clashroyale_agent/qa/` owns parser schema, aliases, fallback/model
orchestration, intent routing, structured-answer orchestration, RAG answering,
evidence grounding, synthesis fallbacks, multi-intent composition, traces and
streaming presentation. `src/clashroyale_agent/api/` owns process/SSE route
wiring, while `src/clashroyale_agent/web/` owns browser proxy and SSE forwarding.
`query_parser.py`, `query_answering.py`, `runtime_multi.py`, and `web_app.py`
remain compatibility facades so existing commands and public payloads do not
change during package migration.

## Routing Principles

- The browser or API request provides the selected `dataset_scope`; the model must not choose or silently change the data range.
- Exact card, deck, matchup, ranking, co-occurrence and full-loadout entity questions should use structured facts after parsing.
- Open-ended meta, archetype or environment questions may use RAG and model synthesis.
- Battle facts, player identifiers and raw logs are never embedded directly.
- Unsupported clan-war schedule/preparation requests remain out of product scope and should not be routed into model synthesis.

## Identifier Contracts

| Mode | Identifier | Boundary |
|---|---|---|
| `base8` | Supercell canonical English card names | Chinese labels are UI aliases, not storage keys |
| `full_loadout` | Official numeric tower/card IDs plus evolution and elite metadata | Must not fall back to base-eight statistics |

See `docs/DATA_CONTRACT.md` for the shared data vocabulary and
`docs/FULL_LOADOUT_DATA_CONTRACT.md` for the full-loadout field contract.

## Structured QA Path

```text
Question
  -> model-first parser with deterministic validation/fallback
  -> skill routing
  -> selected structured snapshot scope
  -> deterministic answer builder
  -> traceable response
```

Structured answers should carry enough context for auditability: selected scope, snapshot group, time window, unique battle count, matched sample size, and any boundary notices.

## RAG Path

```text
Open analysis question
  -> intent validation
  -> scope-filtered hybrid retrieval
  -> RRF fusion and typed lanes
  -> deterministic rerank and diversity selection
  -> evidence compression
  -> model synthesis
  -> numeric and citation validation
  -> final response with verified references
```

Retrieval is bounded. Current behavior uses BM25 and local dense retrieval where available, reciprocal-rank fusion, typed coverage lanes for environment questions, deterministic reranking and diversity selection, then a compressed evidence packet for synthesis.

## Evidence Documents

The RAG corpus uses high-density derived documents rather than one raw battle per chunk. Document families include:

- card profiles
- exact deck profiles
- heuristic archetypes
- card pairs
- observed counters
- deck matchups
- loadout entities
- precomputed meta deltas

Each publication builds the active structured snapshot group and retrievers together. Failed validation keeps the previous group active.

## Grounding and Validation

- Model-written source lists are suppressed and replaced with deterministic verified references.
- Numeric facts must bind to retrieved evidence and preserve precision.
- Document IDs must exist in the selected evidence set.
- Unsupported numeric sentences can be removed locally while preserving the remaining validated answer.
- Final validation failure returns a grounded refusal with verified references, not a generic generation error.

## Streaming UX

The browser should show progress inside the current answer bubble:

- User input appears immediately and is not animated token by token.
- Execution events describe routing, retrieval, rerank, evidence review and model wait status.
- The model-wait step is stable; elapsed seconds update in a compact status line instead of creating repeated transcript rows.
- Only validated public answer text is rendered progressively.
- Private chain-of-thought, raw prompts, request headers, tokens and unvalidated model drafts are not displayed.

## Timeout and Fallback Semantics

- Parser and synthesis calls have bounded timeouts.
- First-public-text timeout returns the already retrieved and validated evidence path rather than starting a second long model call.
- Ollama embedding failures degrade retrieval to deterministic BM25 fallback.
- Provider failures should be visible through model status, circuit state and traceable response metadata.

## Observability

Useful QA/RAG metrics include:

- retrieval candidate count
- selected evidence count
- rerank latency
- first public text latency
- total model latency
- timeout/fallback mode
- validation failure count
- snapshot/RAG fingerprint alignment

See `docs/decisions/ADR-014-bounded-multistage-rag-retrieval.md` for the bounded retrieval decision and `docs/QUALITY_EVALUATION_STRATEGY.md` for evaluation methodology.
