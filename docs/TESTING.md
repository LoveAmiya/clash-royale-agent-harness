# Testing Guide

This project treats testing as layered evidence. Test counts change as features and fixtures change, so README and runbooks should link here instead of copying every number.

## Canonical Commands

```powershell
.\scripts\check_repo.ps1
.\scripts\check_repo.ps1 -Full
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
python -m evaluation.test_inventory
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
- `python -m unittest discover -s tests -t .` is the direct unit/integration
  discovery command. The explicit top-level directory imports the test package
  bootstrap before test modules, so Qdrant, feedback, trace, and quality-report
  artifacts use a per-process temporary directory.
- `run_tests.ps1` is the public quality gate. It runs the unit/integration suite, deterministic evaluation, and synthetic fault-injection checks without private data or external provider calls.
- `evaluation.test_inventory` classifies that same discovery tree into quality
  layers. It is not a second total-test counter and fails if a test module
  cannot be imported.

## Current Public Baseline

| Evidence | Latest known result | Notes |
|---|---:|---|
| Unit/integration discovery | 1152 tests on 2026-08-20 | Canonical package discovery; `OK`, skipped=1 |
| Layer inventory | 1152 tests / 117 classes | Classification of the same unittest discovery tree; not an additional suite |
| Deterministic contract regression | 344/344 enabled cases pass | 4 optional RAG-route cases are skipped by design |
| Snapshot citation and numeric grounding probes | 25/25 pass | Invalid-citation rate `0` in the recorded baseline |
| Synthetic fault injection | 28/28 pass | Covers grounding errors, provider failures, quota/rate limits, stale snapshot/RAG alignment, stream fallback, and Supercell retry cooldown |
| Retrieval ablation | 80 cases | MRR@5 improved from BM25 `0.7556` to Hybrid + rerank `0.9875` in the recorded baseline |

The older `766`, `845`, and `1086` inventory counts belong to earlier test
trees. They are useful historical context, not the current discovery total;
compare only results produced from the same revision and inventory rules.

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
- Use the canonical package-discovery command below. Its bootstrap redirects
  test Qdrant, feedback, trace, and quality-report writes away from repository
  runtime data. A default test that still opens `data/` or an active snapshot
  is an isolation bug and must be fixed instead of worked around by stopping
  the application.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -q
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

For manual report-producing commands, prefer an explicit writable temporary
directory. This avoids failures when `evaluation/reports/` is read-only or
held by another local process:

```powershell
$reportRoot = Join-Path ([System.IO.Path]::GetTempPath()) "clashroyale-agent-reports"
New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
python -m evaluation.test_inventory --report (Join-Path $reportRoot "test-inventory.json")
python -m evaluation.run_fault_injection --report (Join-Path $reportRoot "fault-injection.json")
```

## When to Run Which Gate

- Documentation-only change: run `scripts/check_repo.ps1 -SkipTests` and inspect links/paths.
- Python import or package-layout change: run `python -m unittest discover -s tests -t .`.
- Runtime, parser, RAG, collector, or snapshot behavior change: run `./run_tests.ps1` on Windows PowerShell.
- Live provider or Supercell behavior change: run the credentialed smoke workflow manually from an allowlisted environment.
