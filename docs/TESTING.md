# Testing Guide

This project treats testing as layered evidence. Test counts change as features and fixtures change, so README and runbooks should link here instead of copying every number.

## Canonical Commands

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
python -m evaluation.test_inventory --report evaluation/reports/test-inventory-latest.json
```

- `python -m unittest discover -s tests` is the direct unit/integration discovery command.
- `run_tests.ps1` is the public quality gate. It runs the unit/integration suite, deterministic evaluation, and synthetic fault-injection checks without private data or external provider calls.
- `evaluation.test_inventory` reports the layer inventory and writes a generated report under `evaluation/reports/`, which is ignored by Git.

## Current Public Baseline

| Evidence | Latest known result | Notes |
|---|---:|---|
| Unit/integration discovery | 845 tests on 2026-08-13 | Local Windows run after the supervisor portability fix |
| Deterministic contract regression | 344/344 enabled cases pass | 4 optional RAG-route cases are skipped by design |
| Snapshot citation and numeric grounding probes | 25/25 pass | Invalid-citation rate `0` in the recorded baseline |
| Synthetic fault injection | 28/28 pass | Covers grounding errors, provider failures, quota/rate limits, stale snapshot/RAG alignment, stream fallback, and Supercell retry cooldown |
| Retrieval ablation | 80 cases | MRR@5 improved from BM25 `0.7556` to Hybrid + rerank `0.9875` in the recorded baseline |

The older `766` inventory count belongs to the 2026-08-02 layer report in `docs/QUALITY_EVALUATION_STRATEGY.md`. It is useful historical context, not the current discovery total. Do not compare `766` and `845` as if they were produced by the same test tree.

## Layer Model

| Layer | Scope | Default in public CI |
|---|---|---|
| L0 unit/contract | Parser, aliases, answer builders, small deterministic logic | Yes |
| L1 API/UI integration | FastAPI, SSE, structured UI flows, runtime events, multi-intent orchestration | Yes |
| L2 AI/RAG regression | Golden cases, retrieval benchmark, citation grounding, evidence synthesis | Yes when mocked or deterministic |
| L3 resilience/security/ops | Fault injection, provider failures, Supercell boundaries, Redis quota, deployment and snapshot publication | Yes when deterministic |
| L4 live external smoke | Real model or Supercell calls | No; manual credentialed smoke only |

## CI and Platform Rules

- GitHub Actions public CI runs on Ubuntu.
- Tests that inspect PowerShell scripts as text run on Ubuntu.
- Tests that actually invoke Windows PowerShell must skip unless Windows PowerShell is available.
- Windows-only tests must not read `SystemRoot` at module import time. The skip condition must be applied after safe discovery.
- Public CI must not read `data/corpus/corpus.sqlite`, active private snapshots, real API keys, Supercell tokens, player identifiers, or raw battle logs.

## Generated Reports

Generated reports are operational artifacts, not source files. Keep these ignored:

- `evaluation/reports/`
- `evaluation/*report*.json`
- `evaluation/*.stdout.*`
- `evaluation/*.stderr.*`
- `logs/`
- `tmp/`

If a report contains a finding that should be preserved, summarize the result in a reviewed Markdown document instead of committing the raw generated file.

## When to Run Which Gate

- Documentation-only change: run `git diff --check` and inspect links/paths.
- Python import or package-layout change: run `python -m unittest discover -s tests`.
- Runtime, parser, RAG, collector, or snapshot behavior change: run `./run_tests.ps1` on Windows PowerShell.
- Live provider or Supercell behavior change: run the credentialed smoke workflow manually from an allowlisted environment.
