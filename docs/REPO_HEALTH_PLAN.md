# Repository Health Plan

This plan tracks the work needed to turn the current single-developer, fast-moving repository into a maintainable public project. It is intentionally scoped to repository hygiene, documentation, module boundaries, and quality gates. It does not change collection strategy, API behavior, model routing, or private data handling.

## Baseline

- The project already has strong operational coverage: CI, deterministic evaluation, fault injection, Docker/compose assets, metrics, alerting, and collection runbooks.
- Most root entry modules now delegate to package owners. The former oversized runtime and compatibility surfaces have been reduced to their target boundaries: `runtime_multi.py` is 600 lines, `query_parser.py` is 584 lines, `query_answering.py` is 517 lines, `web_app.py` is a 324-line compatibility route surface, and `web_ui_template.py` is a 20-line alias for the file-backed template. `supercell_live.py` is a compatibility facade with live client/snapshot owners under `src/clashroyale_agent/collection/`.
- Public onboarding is mixed with local operations. `README.md`, `00_START_HERE.md`, and quality docs repeat some testing numbers with different historical contexts.
- Runtime artifacts must remain private: `data/`, `logs/`, `tmp/`, SQLite databases, JSONL traces, generated reports, battle facts, player identifiers, and local zip exports.

## Current Audit - 2026-08-17

This section maps the original cleanup stages to the current repository state.
The numbered phases below are repository-internal cleanup slices, so they do
not exactly match the earlier user-facing stage list.

| Original stage | Current state | Remaining work |
|---|---|---|
| Stage 0 - repository hygiene | In place: ignore rules protect private data, generated reports, logs, SQLite files, JSONL traces, trace and benchmark outputs, and archives; `scripts/check_repo.ps1` audits tracked paths, ignore coverage, staged names, and whitespace. | Keep reviewing staged content manually; automated path checks cannot prove file contents are safe. |
| Stage 1 - directory structure | In place: `src/clashroyale_agent` has API, web, QA, retrieval, stats, collection, snapshots and ops boundaries; current root entry points are compatibility facades. | Keep new behavior in package owners and retain only externally required root compatibility surfaces. |
| Stage 2 - monolith split | Target boundary reached: API schemas, app construction, SSE helpers, status/snapshot helpers, route registration, runtime QA orchestration, collection, and web template/proxy concerns have package owners; no non-test source module exceeds the 600-line giant-file threshold in the current tree scan. | Keep future changes in package owners and re-run the source-size audit after substantial additions. |
| Stage 3 - quality gates | Core gate in place: `pyproject.toml` centralizes package, Ruff, pytest and mypy configuration; `scripts/check_repo.ps1` composes hygiene, compile, unit and full behavioral checks. External API/Supercell/Ollama tests remain opt-in. | Install Ruff/mypy/pytest in the development toolchain before changing their current advisory/config-only status; mark tests incrementally rather than in a bulk rewrite. |
| Stage 4 - documentation refactor | In progress: README, startup guide, architecture, operations, data contract, RAG/QA, testing, ADR index, and contributing docs now have separate owners. | Keep README concise, keep local operations in 00_START_HERE.md/docs/OPERATIONS.md, and keep commit/private-data rules in CONTRIBUTING.md. |

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

- Add this plan, a single canonical testing guide, a contributing guide, Apache-2.0 licensing, and conservative tooling defaults.
- Keep generated evaluation reports and local run outputs ignored.
- Shorten README verification claims and point detailed test numbers to `docs/TESTING.md`.
- Replace personal absolute paths in public-facing instructions with `<repo-root>` placeholders or repo-relative commands.
- Acceptance: `git status --short` shows only reviewed docs/config changes; no data, logs, SQLite, JSONL traces, or secrets are staged.

### Phase 1 - Documentation Split

- Keep README focused on purpose, quick start, commands, architecture links, and data-safety boundaries.
- Create or expand `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/DATA_CONTRACT.md`, `docs/RAG_AND_QA.md`, `docs/TESTING.md`, `docs/decisions/README.md`, and `CONTRIBUTING.md`.
- Move local operator details out of README and keep them in operations/runbook docs.

### Phase 2 - Package Layout

- Introduce `src/clashroyale_agent/` and document the migration pattern in `docs/PACKAGE_MIGRATION.md`.
- Move low-risk support modules first: configuration, logging, runtime events, model gateway, resilience, alerting, and hardening.
- Update imports and tests in small slices.
- Keep PowerShell and Docker entry points compatible.

### Phase 3 - Runtime Split

- Start with a behavior-preserving API schema extraction into
  `src/clashroyale_agent/api/schemas.py`.
- Continue with small API helper extractions for process messages and
  full-loadout repository payload adapters.
- Extract status and observability payload construction for health, model
  provider status, and metrics routes.
- Continue status extraction with live sample settings and compact runtime
  summaries before moving larger readiness and snapshot-status contracts.
- Move the `/ready` response adapter separately from readiness decision logic
  so the route contract stays stable during the larger split.
- Move snapshot artifact and public RAG validation summary helpers before
  splitting the larger `/snapshot/status` response.
- Move RAG/snapshot alignment state into the status helper boundary before
  extracting the larger readiness and snapshot-status decision logic.
- Move small active-snapshot and snapshot-activation helpers before splitting
  the larger snapshot refresh and preheat lifecycle.
- Move live refresh-attempt and collection-progress state writers before
  splitting the larger snapshot refresh loop.
- Move refresh cooldown backoff policy before splitting the larger snapshot
  refresh loop.
- Move volatile live-snapshot runtime display fields before splitting the larger
  `/snapshot/status` response.
- Move refresh-loop delay calculation before extracting the larger live snapshot
  refresh loop.
- Move pre-lock live snapshot refresh gating before extracting the larger
  `ensure_live_snapshot()` body.
- Move live snapshot leaderboard coverage payload construction before splitting
  the larger `/snapshot/status` response.
- Move live snapshot RAG payload construction before splitting the larger
  `/snapshot/status` response.
- Move live snapshot retention and data-source provenance payload construction
  before splitting the larger `/snapshot/status` response.
- Move the top-level live snapshot status response builder before splitting
  route groups out of `runtime_multi.py`.
- Move readiness blocker/degraded-reason decision policy before extracting the
  full `/ready` payload builder.
- Move the full `/ready` payload builder before splitting the remaining
  readiness route and startup lifecycle.
- Move the `/ready` runtime-state reader before extracting health/readiness
  route groups out of `runtime_multi.py`.
- Move startup in-memory snapshot/RAG/live-refresh state initialization before
  extracting the remaining lifespan and route groups.
- Move small RAG ready/bm25 status selection before extracting the larger RAG
  preheat lifecycle.
- Move RAG preheat target-snapshot resolution before extracting the larger RAG
  preheat lifecycle.
- Move non-blocking RAG preheat lock acquisition before extracting the larger
  RAG preheat lifecycle.
- Move RAG candidate build, validation, completion, and failure state writers
  before extracting the larger RAG preheat lifecycle.
- Move RAG candidate document and index identity validation before extracting
  the larger RAG preheat lifecycle.
- Move RAG quality-gate evaluation and report persistence before extracting the
  larger RAG preheat lifecycle.
- Move stale candidate-index discard policy before extracting the larger RAG
  preheat lifecycle.
- Move reusable active RAG retriever detection before extracting the larger RAG
  preheat lifecycle.
- Move validated RAG candidate-index publication and replaced-retriever close
  handling before extracting the larger RAG preheat lifecycle.
- Move persistent RAG snapshot-retention cleanup dispatch before extracting the
  larger RAG preheat lifecycle.
- Move failed candidate-build previous-index reuse policy before extracting the
  larger RAG preheat lifecycle.
- Move the background RAG preheat thread-dispatch entry before extracting the
  larger RAG preheat lifecycle.
- Move lifespan shutdown task cancellation and runtime resource cleanup before
  extracting the remaining route groups.
- Move active RAG retriever selection before extracting the larger RAG preheat
  lifecycle.
- Move fixed live-sample settings policy before extracting the settings route
  group.
- Move structured dataset-scope validation before extracting dataset route
  groups.
- Move active snapshot-group manifest loading and alignment validation before
  extracting dataset route groups.
- Move RAG scope statistics derivation before extracting dataset route groups.
- Move dataset scope display labels and unavailable dataset payload construction before extracting dataset route groups.
- Move dataset catalog response payload construction before extracting dataset route groups.
- Move structured snapshot-group repository cache resolution before extracting dataset route groups.
- Move official snapshot structured repository cache resolution before extracting dataset route groups.
- Move rolling dataset RAG retriever cache/loading before extracting dataset route groups.
- Extract structured API route registration before splitting the remaining route groups.
- Extract feedback route registration before splitting the remaining route groups.
- Extract health/readiness/model-status/metrics route registration before splitting the remaining route groups.
- Extract live-sample settings route registration before splitting the remaining route groups.
- Extract snapshot status route registration before splitting the remaining route groups.
- Extract process/SSE route registration before splitting the remaining route groups.
- Split `runtime_multi.py` into API app, route groups, startup/preheat, snapshot state, SSE, quota, feedback, and health/admin modules.
- Keep route contracts and response payloads stable.
- Add regression tests around any route or SSE contract touched during the split.

### Phase 4 - QA/RAG Split

- Extract model stream first-public-text watchdog before splitting synthesis orchestration.
- Extract intent and metric schema constants before splitting fallback parsing.
- Extract metric parsing helpers before splitting fallback parser orchestration.
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
- Repository hygiene gate: `./scripts/check_repo.ps1`; use `-Full` before a reviewable phase boundary.
- Unit/integration discovery: `python -m unittest discover -s tests`.
- Testing numbers must link to `docs/TESTING.md` instead of being repeated across multiple docs.
- Windows-only tests must skip at runtime when Windows PowerShell is unavailable; they must not fail during module import on Linux.
- Live model or Supercell checks must remain opt-in and credentialed.

## Review Rules

- Keep each change focused: docs, ignore rules, package moves, runtime split, QA split, collector split, or cleanup.
- Do not mix behavior changes with file moves.
- Do not stage private data or generated reports.
- Prefer small commits that can be reverted independently.

The active continuation queue for the unfinished cleanup work lives in
`docs/REFACTOR_QUEUE.md`. Recurring Codex runs should use that file as the
source of truth for the next slice and for interruption recovery.

## Remaining Risks After This Cleanup

- The compatibility facades listed in the current audit remain larger than the
  preferred module targets. Do not collapse package ownership back into them;
  reduce them only in tested, behavior-preserving slices.
- Ruff, mypy, and pytest configuration exists, but Ruff and mypy are currently
  advisory until installed in the development environment or CI image.
- `scripts/check_repo.ps1 -Full -IncludeUnstaged` currently reports CRLF
  whitespace on pre-existing full-file edits to `query_answering.py` and
  `query_parser.py`. Normalize those files in a dedicated formatting-only
  change after checking the Windows compatibility diff; do not mix it with a
  behavioral refactor.
- Queue item 33 is intentionally blocked: no dead-code candidate has owner
  confirmation that it is unused by Windows tasks or operators. No business
  code or runtime output was deleted during this cleanup.
- Performance baselines expose aggregate measurements only. Live provider and
  collector runs remain opt-in; compare their generated local reports outside
  version control.
- `LICENSE` and project metadata now declare Apache-2.0. Keep the license text,
  copyright notices, and third-party attribution boundaries intact.
