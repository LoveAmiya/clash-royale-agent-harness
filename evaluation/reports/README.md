# Evaluation Reports

This directory is for local generated evaluation and benchmark output. Reports
are evidence for a run, not source artifacts: do not commit, push, paste, or
attach them when they may contain private paths, prompts, request metadata,
player identifiers, battle facts, provider details, or raw traces.

## Report Classes

- Deterministic contract reports: parser/routing/answer cases and failure counts.
- Fault-injection reports: offline resilience and degradation scenarios.
- Retrieval reports: candidate counts, latency, hit-rate and method comparisons.
- Citation/grounding reports: evidence and unsupported-fact checks.
- Live smoke reports: opt-in real-provider checks; keep them local and redacted.
- Scorecards: normalized summaries built from already reviewed local reports.

## Commands

Run from the repository root. Use `--help` first to inspect the command's
current options and choose an explicit report path under this directory.

```powershell
python -m evaluation.run_eval --help
python -m evaluation.run_fault_injection --help
python -m evaluation.retrieval_benchmark --help
python -m evaluation.citation_benchmark --help
python -m evaluation.scorecard --help
```

The public gate invokes deterministic evaluation and fault injection without
real Supercell, OpenAI, or Ollama requests. Live API, live RAG, and live LLM
commands require explicit credentials and are not part of the default gate:

```powershell
python -m evaluation.run_live_api_smoke --help
python -m evaluation.run_live_rag_smoke --help
python -m evaluation.run_live_llm_eval --help
```

Prefer descriptive local names such as
`evaluation/reports/retrieval-<date>.json`. Do not overwrite a report while a
run is active; use the command's atomic writer or a temporary sibling file.

## Retention and Review

`.gitignore` covers this directory and report-like files elsewhere in
`evaluation/`. Before sharing a summary, remove raw paths, credentials, request
headers, player tags, battle payloads and full model traces. Keep only aggregate
metrics and a link to the code/config revision used for the run.

When a report reveals a regression, add a deterministic fixture or test first;
do not turn the generated JSON into a checked-in golden file. The final review
must inspect `git status --short` and confirm that no report, trace, SQLite,
JSONL, log, archive or private data path is newly tracked.
