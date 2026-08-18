# Testing Guide

This project treats testing as layered evidence. Test counts change as features and fixtures change, so README and runbooks should link here instead of copying every number.

## Canonical Commands

```powershell
.\scripts\check_repo.ps1
.\scripts\check_repo.ps1 -Full
.\.venv\Scripts\python.exe -m unittest discover -s tests
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
python -m evaluation.test_inventory --report evaluation/reports/test-inventory-latest.json
```

- `scripts/check_repo.ps1` is the repository hygiene gate. By default it
  checks staged whitespace, tracked private-output paths, ignore coverage,
  staged secret-like path names, Python compilation, and test discovery.
- `scripts/check_repo.ps1 -Full` delegates the behavioral portion to
  `run_tests.ps1`. Use `-IncludeUnstaged` only when the whole worktree is
  expected to be clean enough for an unstaged whitespace check.
- Ruff runs automatically when installed. A missing Ruff executable is
  reported as a warning so existing local environments remain usable while
  the toolchain migration is completed.
- Optional local tooling dependencies live in `requirements-dev.txt`. They are
  separate from runtime dependencies so live provider, Supercell, and Ollama
  checks do not become default install requirements.
- `python -m unittest discover -s tests` is the direct unit/integration discovery command.
- `run_tests.ps1` is the public quality gate. It runs the unit/integration suite, deterministic evaluation, and synthetic fault-injection checks without private data or external provider calls.
- `evaluation.test_inventory` reports the layer inventory and writes a generated report under `evaluation/reports/`, which is ignored by Git.

## Current Public Baseline

| Evidence | Latest known result | Notes |
|---|---:|---|
| Unit/integration discovery | 1086 tests on 2026-08-18 | Local Windows run after repository/package migration and QA-boundary checks; skipped=1 |
| Deterministic contract regression | 344/344 enabled cases pass | 4 optional RAG-route cases are skipped by design |
| Snapshot citation and numeric grounding probes | 25/25 pass | Invalid-citation rate `0` in the recorded baseline |
| Synthetic fault injection | 28/28 pass | Covers grounding errors, provider failures, quota/rate limits, stale snapshot/RAG alignment, stream fallback, and Supercell retry cooldown |
| Retrieval ablation | 80 cases | MRR@5 improved from BM25 `0.7556` to Hybrid + rerank `0.9875` in the recorded baseline |

The older `766` and `845` inventory counts belong to earlier test trees. They
are useful historical context, not the current discovery total; compare only
results produced from the same revision and inventory rules.

## Layer Model

| Layer | Scope | Default in public CI |
|---|---|---|
| L0 unit/contract | Parser, aliases, answer builders, small deterministic logic | Yes |
| L1 API/UI integration | FastAPI, SSE, structured UI flows, runtime events, multi-intent orchestration | Yes |
| L2 AI/RAG regression | Golden cases, retrieval benchmark, citation grounding, evidence synthesis | Yes when mocked or deterministic |
| L3 resilience/security/ops | Fault injection, provider failures, Supercell boundaries, Redis quota, deployment and snapshot publication | Yes when deterministic |
| L4 live external smoke | Real model or Supercell calls | No; manual credentialed smoke only |

`pyproject.toml` declares the planned pytest markers `unit`, `integration`,
`windows`, `collector`, `rag`, and `live_api`. Unittest discovery remains the
compatibility runner until tests are marked incrementally. `live_api` must stay
excluded from default pytest runs.

## CI and Platform Rules

- GitHub Actions public CI runs on Ubuntu.
- Tests that inspect PowerShell scripts as text run on Ubuntu.
- Tests that actually invoke Windows PowerShell must skip unless Windows PowerShell is available.
- Windows-only tests must not read `SystemRoot` at module import time. The skip condition must be applied after safe discovery.
- Public CI must not read `data/corpus/corpus.sqlite`, active private snapshots, real API keys, Supercell tokens, player identifiers, or raw battle logs.
- Before running full local unittest discovery against the checked-out repository, stop local API/Web processes on ports `8091` and `8080` when they are using the local Qdrant index. Otherwise the running API process can hold the Qdrant file lock and produce false failures unrelated to the code under test.

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8080,8091 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }

.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

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

- Documentation-only change: run `scripts/check_repo.ps1 -SkipTests` and inspect links/paths.
- Python import or package-layout change: run `python -m unittest discover -s tests`.
- Runtime, parser, RAG, collector, or snapshot behavior change: run `./run_tests.ps1` on Windows PowerShell.
- Live provider or Supercell behavior change: run the credentialed smoke workflow manually from an allowlisted environment.
