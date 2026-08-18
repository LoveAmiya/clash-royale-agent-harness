# Architecture

This document explains the current system shape and the target boundaries for the repository cleanup. It describes the system as it exists today; package moves and file splits are tracked separately in `docs/REPO_HEALTH_PLAN.md`.

## Runtime Roles

| Role | Entry point | Responsibility | External calls | Writes private data |
|---|---|---|---|---|
| Browser UI | `run_web.ps1` -> `web_app.py` | Serves the local web interface and proxies requests to the API | No direct model or Supercell calls | No |
| API | `run_api.ps1` -> `runtime_multi.py` | Reads the active snapshot group, answers structured and RAG-backed questions, exposes health/status/metrics | Model calls only when configured routes need parsing or synthesis | No collection writes |
| Collector | scheduled PowerShell tasks -> `scripts/run_daily_ranked_schedule.ps1` -> `scripts/collect_rolling_corpus.py` | Calls Supercell, writes rolling facts, validates, materializes and publishes snapshots | Supercell only; local Ollama during publication | Yes |
| Evaluation | `run_tests.ps1`, `evaluation/*` | Runs deterministic tests, golden cases, retrieval checks and failure injection | No external calls in public gate | Generated reports only, ignored by Git |

API and collector roles are intentionally split. Normal web/API startup must not start collection, and collection should not require Codex, a browser, or a chat session to stay open.

The API package owns application construction, route facades, lifecycle,
preheat, health/status, snapshot and SSE helpers. `runtime_multi.py` remains
the compatible API entry point and dependency assembler. Collection preflight,
Supercell transport, battle parsing, loadout normalization, rolling corpus and
materialization are package-owned; their root modules and scheduled scripts are
compatible entry points.

## Data Flow

```text
Supercell API
  -> daily_ranked / weekly_expanded collectors
  -> data/rolling_lanes/<mode>/active checkpoints
  -> data/corpus/corpus.sqlite
  -> rolling materializer
  -> data/snapshot_groups/<snapshot_group_id>/
  -> active snapshot pointer
  -> API structured answers and scope-filtered RAG
  -> browser UI / API clients
```

Raw battle logs and player identifiers remain private runtime data. Public source control contains only code, tests, documentation, safe config templates, and the reviewed Chinese alias catalog.

## Query Flow

```text
User question
  -> parser and validation
  -> intent router
  -> structured SQLite path for exact stats/rankings/matchups/entities
  -> scope-filtered RAG path only for open meta/archetype/environment analysis
  -> evidence and numeric validation
  -> traceable API response and browser rendering
```

Exact card, deck, matchup, ranking, co-occurrence and loadout-entity questions should resolve through deterministic structured facts after parsing. Open-ended environment analysis may use RAG plus model synthesis, but the selected `dataset_scope` remains a hard filter.

## Current Module Map

| Area | Package owner | Compatible root entry points |
|---|---|---|
| API lifecycle and routes | `src/clashroyale_agent/api/`: app/runtime, route registration, startup, preheat, status, snapshots, SSE and schemas | `runtime_multi.py` |
| Browser UI | `src/clashroyale_agent/web/`: file-backed template, request schemas, HTTP proxy helpers, SSE forwarding and startup factory | `web_app.py`, `web_ui_template.py` |
| QA and intent parsing | `src/clashroyale_agent/qa/`: parser schema/aliases/fallbacks, intent routing, structured answering, RAG orchestration, evidence grounding and presentation | `query_parser.py`, `query_answering.py` |
| Retrieval | `src/clashroyale_agent/retrieval/` plus existing retrieval compatibility modules | `hybrid_retriever.py`, `retrieval_postprocess.py`, `rag_quality.py`, `rag_data_builder.py` |
| Structured statistics | `src/clashroyale_agent/stats/` | `structured_query.py`, `structured_stats.py` |
| Collection and materialization | `src/clashroyale_agent/collection/`: preflight, Supercell client, battle parsing, loadout normalization, rolling corpus and materializer | `supercell_live.py`, `rolling_corpus.py`, `rolling_materializer.py`, `scripts/collect_rolling_corpus.py` |
| Snapshots | `src/clashroyale_agent/snapshots/` | `snapshot_store.py`, `snapshot_audit.py` |
| Operations | `src/clashroyale_agent/ops/` | `runtime_hardening.py`, `model_gateway.py`, `model_resilience.py`, `feedback_store.py`, `alert_receiver.py`, `logging_config.py` |

Compatibility entry points preserve existing PowerShell commands, imports,
scheduled-task targets, API contracts, and SSE payloads. New implementation
work belongs in the package owner unless a compatibility facade is required.

## Publication Invariants

- A new snapshot group becomes active only after structured facts, RAG documents, fingerprints, validation gates and scope metadata align.
- The API reads the active snapshot group; it does not mutate the rolling corpus.
- Collection batches globally deduplicate by `battle_id`.
- `base8` and `full_loadout` identifiers are separate contracts and must not silently fall back to each other.
- Failed publication keeps the previous active group serving traffic.

## Operational Boundaries

- `/health` confirms process health; readiness and data quality must be checked through the richer status endpoints and dashboard.
- `/metrics`, model status and feedback statistics are operational surfaces and should be exposed only inside a trusted boundary or behind an explicit proxy/auth layer.
- Provider keys, Supercell tokens, raw logs, player tags and battle facts must not appear in source, docs examples, commits, container images or pasted reports.

See also: `docs/OPERATIONS.md`, `docs/DATA_CONTRACT.md`, `docs/RAG_AND_QA.md`, `docs/FULL_LOADOUT_DATA_CONTRACT.md`, and `docs/SNAPSHOT_COLLECTION_HANDOFF.md`.
