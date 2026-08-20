# Package Migration Guide

This guide controls Phase 2 of the repository cleanup: moving the current
root-level Python modules into `src/clashroyale_agent/` without changing
runtime behavior.

## Current Status

The package namespace now exists under `src/clashroyale_agent/` with boundary
packages for API, web, QA, retrieval, stats, collection, snapshots, and
operations. The first implementation moved is `logging_config.py`, now backed
by `clashroyale_agent.ops.logging_config` with the root module kept as a
compatibility wrapper.

Moved modules:

- FastAPI application construction now lives in
  `clashroyale_agent.api.app`; `runtime_multi.py` supplies runtime
  dependencies and retains the public `app` object.
- Startup-owned metrics, feedback cache/store, process quota creation, and
  quota probing now live in `clashroyale_agent.api.lifecycle` through
  `initialize_runtime_services`; `runtime_multi.py` supplies configuration and
  factories without changing startup order.
- RAG preheat orchestration now lives in `clashroyale_agent.api.rag_preheat`
  through `preheat_rag_retriever` and an explicit dependency bundle;
  `runtime_multi.py` remains the compatibility facade for existing monkeypatch
  tests and runtime configuration.
- API route, startup, and health compatibility surfaces now live in
  `clashroyale_agent.api.routes`, `startup`, and `health`.
- SSE data framing and stream chunk splitting now live in
  `clashroyale_agent.api.sse`; route event order and payloads are unchanged.
- `supercell_preflight.py` -> `clashroyale_agent.collection.preflight`.
  The root module remains an identity-preserving import alias and keeps
  `python -m supercell_preflight` compatible.

- `logging_config.py` -> `clashroyale_agent.ops.logging_config`.
  Old imports still re-export `SecretRedactionFilter`, `JsonFormatter`, and
  `configure_logging`.
- `runtime_events.py` -> `clashroyale_agent.ops.runtime_events`.
  Old imports still re-export `RuntimeEventEmitter`.
- `model_resilience.py` -> `clashroyale_agent.ops.model_resilience`.
  Old imports still re-export `ModelProviderGuard`, `ModelCircuitOpenError`,
  and `ModelStreamingUnavailableError`.
- `model_gateway.py` -> `clashroyale_agent.ops.model_gateway`.
  Old imports still re-export model generation, stream, provider status, metrics,
  and test guard replacement helpers.
- `runtime_hardening.py` -> `clashroyale_agent.ops.runtime_hardening`.
  Old imports still re-export request body limits, request/admin safety helpers,
  in-memory and Redis quota classes, quota factory, and runtime metrics.
- `feedback_store.py` -> `clashroyale_agent.ops.feedback_store`.
  Old imports still re-export the bounded recent-answer cache and durable
  feedback store.
- `app_config.py` -> `clashroyale_agent.ops.app_config`.
  Old imports keep exposing environment-backed runtime, model, retrieval,
  feedback and Supercell collection configuration.
- `alert_receiver.py` -> `clashroyale_agent.ops.alert_receiver`.
  Old imports keep exposing the Alertmanager receiver app and alert persistence
  helpers.
- API request schemas now live in `clashroyale_agent.api.schemas`; runtime
  imports keep exposing the request model names for existing tests and callers.
- Process-message and full-loadout payload adapters now live in
  `clashroyale_agent.api.messages` and `clashroyale_agent.api.payloads`,
  keeping `runtime_multi.py` focused on route orchestration.
- Health, model status, and Prometheus metrics payload helpers now live in
  `clashroyale_agent.api.status`, keeping those route contracts stable while
  moving response construction out of `runtime_multi.py`.
- Live sample settings and compact runtime summaries now use the same
  `clashroyale_agent.api.status` helper boundary, preserving route names while
  shrinking status payload construction in `runtime_multi.py`.
- The `/ready` route now uses a small readiness response adapter in
  `clashroyale_agent.api.status`; the readiness decision logic remains in
  `runtime_multi.py` for a later, larger split.
- Snapshot artifact readiness and public RAG validation summaries now live in
  `clashroyale_agent.api.status`; `runtime_multi.py` keeps compatibility
  wrappers for existing tests and route code.
- RAG/snapshot alignment state now has a package helper in
  `clashroyale_agent.api.status`, with `runtime_multi.py` retaining the
  existing private wrapper while larger snapshot-status extraction continues.
- Small snapshot state helpers now live in `clashroyale_agent.api.snapshot_state`;
  `runtime_multi.py` keeps existing private wrappers for active snapshot ids and
  validated snapshot activation.
- Live refresh-attempt and collection-progress state writers now live in
  `clashroyale_agent.api.snapshot_state`; `runtime_multi.py` retains wrappers
  for existing logging and refresh lifecycle calls.
- Live refresh cooldown backoff now lives in
  `clashroyale_agent.api.snapshot_state`, keeping retry timing policy near the
  other snapshot lifecycle state helpers while preserving the runtime wrapper.
- Live snapshot runtime display fields now live in
  `clashroyale_agent.api.status`, so `/snapshot/status` no longer reaches
  directly into volatile refresh, progress, cooldown, and error state.
- Live refresh-loop delay calculation now lives in
  `clashroyale_agent.api.snapshot_state`, keeping sleep/backoff decisions
  testable before the larger refresh loop moves out of `runtime_multi.py`.
- Live snapshot refresh gating now lives in
  `clashroyale_agent.api.snapshot_state`, separating cooldown/cache decisions
  from the network fetch and publication body in `runtime_multi.py`.
- Live snapshot leaderboard coverage payloads now live in
  `clashroyale_agent.api.status`, preserving the `/snapshot/status` response
  while reducing inline status assembly in `runtime_multi.py`.
- Live snapshot RAG payload construction now lives in
  `clashroyale_agent.api.status`, keeping validation sampling, candidate
  status, quality, and alignment display fields in one tested helper.
- Live snapshot retention and data-source provenance payloads now live in
  `clashroyale_agent.api.status`, leaving fewer static response fragments in
  `runtime_multi.py`.
- The top-level live snapshot status response builder now lives in
  `clashroyale_agent.api.status`; `runtime_multi.py` now prepares runtime
  dependencies and delegates the `/snapshot/status` payload contract.
- The readiness status decision helper now lives in
  `clashroyale_agent.api.status`, separating blocker/degraded-reason policy
  from the remaining `/ready` payload assembly in `runtime_multi.py`.
- The full `/ready` payload builder now lives in
  `clashroyale_agent.api.status`; `runtime_multi.py` only collects runtime
  state and delegates the stable readiness contract.
- The full `/ready` runtime-state reader now lives in
  `clashroyale_agent.api.status`; `runtime_multi.py` keeps the public helper
  name while delegating quota fallback, snapshot usability, and RAG alignment
  assembly to the package boundary.
- Small RAG readiness status selection now lives in
  `clashroyale_agent.api.snapshot_state`, keeping dense-vs-BM25 public status
  policy outside the main RAG preheat flow.

## Migration Rules

- Move modules in small slices that keep the project runnable after each step.
- Do not mix file moves with behavior changes.
- Keep PowerShell scripts, Docker entry points, and public API routes compatible.
- Keep old import paths working with thin compatibility wrappers until callers
  and tests are moved.
- Run the relevant tests after each slice.
- Do not move private data, generated reports, logs, SQLite databases, or JSONL
  traces into the package tree.

## Suggested Order

Start with low-risk modules that have narrow responsibilities and clear tests:

1. configuration helpers
2. logging setup
3. runtime event models
4. model gateway and resilience helpers
5. hardening and request-safety helpers
6. alert and feedback helpers

Only after those moves are stable should the project split `runtime_multi.py`,
`query_parser.py`, `query_answering.py`, `supercell_live.py`, `web_app.py`, and
`web_ui_template.py`.

## Compatibility Pattern

When moving a root module, prefer this sequence:

1. create the new module under `src/clashroyale_agent/...`
2. move implementation code without changing behavior
3. leave the root module as a compatibility wrapper that imports and re-exports
   the moved public names
4. update tests and direct internal imports in a separate commit
5. remove the compatibility wrapper only after no supported entry point imports it

This keeps stack traces, scripts, and external callers stable during the
transition.

## Verification

Minimum checks for a package-migration slice:

~~~powershell
python -m compileall -q src *.py evaluation harness planner skills tests
python -m unittest discover -s tests -t .
powershell -ExecutionPolicy Bypass -File .\scripts\check_repo.ps1
~~~

Run the full public gate when touching runtime, parser, RAG, collector,
snapshot, or publication behavior:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
~~~

## Non-Goals

- No public API shape changes.
- No collection scheduling changes.
- No RAG retrieval or prompt changes.
- No dependency additions unless the slice specifically needs one and documents why.
- No dead-code deletion without a separate reviewed candidate list.
