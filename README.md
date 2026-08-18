# Clash Royale Agent Harness

Clash Royale Agent Harness is a local FastAPI system for answering Clash Royale
card, deck, matchup, entity-loadout, and meta questions. It combines structured
statistics, scope-filtered RAG, deterministic evidence validation, SSE progress,
and a browser UI.

This repository contains public code and documentation only. It does not publish
private battle facts, player identifiers, local snapshots, SQLite databases,
generated reports, logs, API keys, PushPlus tokens, or Supercell tokens.

## Core Capabilities

- Answers exact card, deck, matchup, ranking, co-occurrence, and loadout-entity questions from local structured facts.
- Uses RAG and model synthesis for open-ended meta, archetype, and environment analysis.
- Keeps `dataset_scope` explicit across API and browser requests.
- Publishes structured facts and RAG documents as one validated snapshot group.
- Streams traceable execution progress without exposing raw prompts, credentials, private chain-of-thought, or unvalidated drafts.

## Quick Start

Run from the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
```

The public gate does not require a model key, Supercell token, private SQLite
snapshot, or unpublished battle data. Create `.env` from `.env.example` only
when optional providers are needed; never commit real credentials.

## Run Locally

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1
powershell -ExecutionPolicy Bypass -File .\run_web.ps1
```

| Surface | URL |
|---|---|
| Browser UI | `http://127.0.0.1:8080` |
| API health | `http://127.0.0.1:8091/health` |
| API process endpoint | `http://127.0.0.1:8091/process` |

API startup is read-only and follows the active published snapshot group.
Collection is a separate manual or Windows scheduled-task workflow.

## Data Boundary

Public source control contains application code, deterministic tests, safe
fixtures, documentation, ADRs, configuration templates, and the reviewed
Chinese alias catalog under `data/card_aliases.zh-CN.json`.

The following remain local and ignored by Git: corpus and snapshot data, logs,
temporary files, SQLite databases, JSONL traces, generated reports, archives,
benchmark output, provider credentials, request headers, raw battle logs, and
player tags. Read [operations](docs/OPERATIONS.md) and the
[data contract](docs/DATA_CONTRACT.md) before changing collection, publication,
or reporting behavior.

## Project Layout

```text
src/clashroyale_agent/  Package implementations: API, web, QA, retrieval, stats, collection, snapshots, ops
scripts/                Manual and scheduled-task entry points
tests/                  Deterministic unit and integration coverage
evaluation/             Deterministic evaluation cases and benchmark harnesses
docs/                   Architecture, operations, contracts, testing, and ADRs
deploy/                 Deployment support files
```

Legacy root modules remain compatibility entry points while behavior-preserving
package migration proceeds. Do not change collection strategy, scheduled-task
contracts, RAG behavior, public APIs, or SSE payloads as part of a file move.

## Documentation

- [Start here](00_START_HERE.md): local startup, recovery, and common commands.
- [Architecture](docs/ARCHITECTURE.md): runtime roles, data flow, and module boundaries.
- [Operations](docs/OPERATIONS.md): private data, configuration, deployment, and recovery boundaries.
- [Data contract](docs/DATA_CONTRACT.md): dataset scopes, snapshots, facts, identifiers, and deduplication.
- [RAG and QA](docs/RAG_AND_QA.md): routing, evidence, grounding, SSE, timeouts, and fallbacks.
- [Testing](docs/TESTING.md): test layers, generated-report policy, and live-smoke boundary.
- [Collection handoff](docs/SNAPSHOT_COLLECTION_HANDOFF.md): collection, alerts, and Windows recovery steps.
- [Contributing](CONTRIBUTING.md): quality gates, commit scope, and private-data rules.
- [Repository health plan](docs/REPO_HEALTH_PLAN.md): package migration and cleanup policy.
- [Architecture decisions](docs/decisions/README.md): ADR index.

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
