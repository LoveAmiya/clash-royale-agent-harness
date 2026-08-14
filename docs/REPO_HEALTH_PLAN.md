# Repository Health Plan

This plan tracks the work needed to turn the current single-developer, fast-moving repository into a maintainable public project. It is intentionally scoped to repository hygiene, documentation, module boundaries, and quality gates. It does not change collection strategy, API behavior, model routing, or private data handling.

## Baseline

- The project already has strong operational coverage: CI, deterministic evaluation, fault injection, Docker/compose assets, metrics, alerting, and collection runbooks.
- Core source files are still root-level and oversized. The highest-risk files are `runtime_multi.py`, `query_parser.py`, `query_answering.py`, `supercell_live.py`, `web_app.py`, and `web_ui_template.py`.
- Public onboarding is mixed with local operations. `README.md`, `00_START_HERE.md`, and quality docs repeat some testing numbers with different historical contexts.
- Runtime artifacts must remain private: `data/`, `logs/`, `tmp/`, SQLite databases, JSONL traces, generated reports, battle facts, player identifiers, and local zip exports.

## Non-Goals

- Do not change collector scheduling, token selection, deduplication, or publication logic in this phase.
- Do not rewrite RAG, model prompts, SSE behavior, or structured query behavior in this phase.
- Do not delete candidate dead code until it is listed, reviewed, and separated into a cleanup-only change.
- Do not make private data or generated reports part of source control.

## Target Shape

```text
src/clashroyale_agent/
  api/              FastAPI app, routes, startup, SSE, health, admin boundaries
  web/              browser UI service, templates, static assets
  qa/               parser, intent schema, answer synthesis, evidence ledger
  retrieval/        hybrid retrieval, rerank, postprocess, compression
  stats/            structured query and deterministic statistics
  collection/       Supercell client, rolling corpus, materializer, preflight
  snapshots/        snapshot store, audit, publication state
  ops/              logging, resilience, alerts, hardening, quota helpers
scripts/            human and scheduled-task entry points
tests/              unit, integration, RAG, ops, and Windows-specific tests
docs/               architecture, operations, data contracts, testing, ADRs
evaluation/         deterministic cases and non-private benchmark harnesses
deploy/             production deployment assets
```

The first migration should move modules without changing behavior. Function-level refactors come later, after import paths and entry points are stable.

## Phases

### Phase 0 - Repository Hygiene

- Add this plan and a single canonical testing guide.
- Keep generated evaluation reports and local run outputs ignored.
- Shorten README verification claims and point detailed test numbers to `docs/TESTING.md`.
- Replace personal absolute paths in public-facing instructions with `<repo-root>` placeholders or repo-relative commands.
- Acceptance: `git status --short` shows only reviewed docs/config changes; no data, logs, SQLite, JSONL traces, or secrets are staged.

### Phase 1 - Documentation Split

- Keep README focused on purpose, quick start, commands, architecture links, and data-safety boundaries.
- Create or expand `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/DATA_CONTRACT.md`, `docs/RAG_AND_QA.md`, `docs/TESTING.md`, and `docs/decisions/README.md`.
- Move local operator details out of README and keep them in operations/runbook docs.

### Phase 2 - Package Layout

- Introduce `src/clashroyale_agent/`.
- Move low-risk support modules first: configuration, logging, runtime events, model gateway, resilience, alerting, and hardening.
- Update imports and tests in small slices.
- Keep PowerShell and Docker entry points compatible.

### Phase 3 - Runtime Split

- Split `runtime_multi.py` into API app, route groups, startup/preheat, snapshot state, SSE, quota, feedback, and health/admin modules.
- Keep route contracts and response payloads stable.
- Add regression tests around any route or SSE contract touched during the split.

### Phase 4 - QA/RAG Split

- Split parser, fallback parser, intent schema, evidence ledger, synthesis, retrieval orchestration, and streaming presentation.
- Preserve deterministic fallback behavior and evidence-boundary validation.
- Keep model-call timeout, first-public-text timeout, and degradation semantics visible in tests.

### Phase 5 - Collector Split

- Split Supercell client, preflight, battle parsing, loadout normalization, rolling corpus writes, materialization, and notification/reporting.
- Keep scheduled task names, token-slot behavior, one-hop expansion, and `battle_id` global deduplication unchanged unless a dedicated collector change says otherwise.

### Phase 6 - Dead Code and Dependency Review

- Produce a dead-code candidate report before deletion.
- Separate cleanup commits from feature or refactor commits.
- Audit heavyweight dependencies and document which ones are direct, optional, or inherited.
- Track cold start time, install time, Docker image size, and benchmark runtime before removing or replacing dependencies.

## Quality Gates

- Public deterministic gate: `./run_tests.ps1` on Windows PowerShell.
- Unit/integration discovery: `python -m unittest discover -s tests`.
- Testing numbers must link to `docs/TESTING.md` instead of being repeated across multiple docs.
- Windows-only tests must skip at runtime when Windows PowerShell is unavailable; they must not fail during module import on Linux.
- Live model or Supercell checks must remain opt-in and credentialed.

## Review Rules

- Keep each change focused: docs, ignore rules, package moves, runtime split, QA split, collector split, or cleanup.
- Do not mix behavior changes with file moves.
- Do not stage private data or generated reports.
- Prefer small commits that can be reverted independently.
