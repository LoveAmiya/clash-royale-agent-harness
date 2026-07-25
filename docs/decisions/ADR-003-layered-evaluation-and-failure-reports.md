# ADR-003: Layered Evaluation With Durable Failure Reports

## Status

Accepted

## Context

The original local evaluation checked a small, repetitive collection of routing
prompts in one loop. It did not make expected intent, field, Skill, or answer
mismatches fail the process, and it left no durable error artifact. That gave a
green test result without proving the multi-intent, official-snapshot, RAG, or
streaming product claims.

## Decision

The default quality gate has two deterministic layers:

1. `unittest discover` covers units and local integrations: parsing and aliases,
   deterministic Skills, multi-intent orchestration, daily snapshots, RAG
   evidence/preheat, model-stream adapters, runtime events, SSE output, and
   Trace contracts.
2. `evaluation/cases.jsonl` is a reviewed, static corpus of at least 300
   independently executable cases. It checks intent, routed Skill, parsed fields,
   multi-intent subqueries, and required answer evidence. Its generator is a
   maintenance tool only and is never run by the test command.

`evaluation.run_eval` records all assertion failures on each case, writes a new
timestamped JSON report under `evaluation/reports/`, and exits non-zero whenever
there is a non-skipped failure. Reports are retained for diagnosis rather than
being replaced by a passing run.

External-system confidence is separate. `RUN_LIVE_API_SMOKE=true` adds the real
backend smoke test, which requires configured model and Supercell credentials
and validates API provenance, RAG synthesis, SSE, and final Trace metadata.

When the dense embedding service is available, the snapshot retrieval benchmark
builds silver-label cases from current card, deck, archetype, pair, counter, and
matchup evidence. It refuses to report a BM25-only run as hybrid retrieval.

## Consequences

- A local green run is fast, deterministic, and does not spend API quota.
- Routing and answer regressions have stable case IDs and actionable error
  messages.
- Optional RAG cases validate parse and routing without silently making a model
  request; full RAG generation remains covered by local integration tests and
  the explicit live smoke test.
- The test count reflects independently reported cases, not repeated prompts
  hidden behind one loop.
- Updating a snapshot may require a reviewed update to the static corpus or
  expected answer evidence; regenerating it during a test run is prohibited.
