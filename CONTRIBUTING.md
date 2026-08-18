# Contributing

This repository is the public code and documentation surface for the Clash
Royale Agent Harness. Contributions should improve maintainability without
publishing private runtime data or changing operational behavior by accident.

## Scope Rules

- Keep each change focused: docs, tooling, API behavior, RAG behavior,
  collection behavior, data contracts, or deployment.
- Do not mix behavior changes with file moves or formatting-only rewrites.
- Do not refactor adjacent code unless the task requires it.
- Keep collection scheduling, token slot behavior, one-hop expansion, global
  battle deduplication, and publication gates unchanged unless the change is
  explicitly about collection.
- Keep model routing, prompt construction, evidence validation, and SSE contract
  unchanged unless the change is explicitly about QA/RAG.

## Private Data and Secrets

Never commit or paste these into source control, issues, logs, examples, or
container images:

- .env, API keys, admin keys, provider tokens, PushPlus tokens, or Supercell tokens
- raw battle logs, player tags, request headers, full provider prompts, or raw traces
- SQLite databases, JSONL runtime traces, local snapshots, generated reports, or exports
- data/corpus/, data/rolling_lanes/, data/snapshot_groups/,
  data/structured_stats/, logs/, tmp/, and evaluation/reports/

If a generated report contains an important result, summarize the finding in a
reviewed Markdown document instead of committing the raw report.

## Local Setup

Run from the repository root:

~~~powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
~~~

Optional provider and collector credentials belong in the local process,
Windows user environment, .env, or deployment secret store. Do not commit
real credentials.

## Quality Gates

Use the smallest gate that matches the change:

| Change type | Minimum verification |
|---|---|
| Docs only | powershell -ExecutionPolicy Bypass -File .\scripts\check_repo.ps1 -SkipTests plus link/path review |
| Python import or portability change | python -m unittest discover -s tests |
| Runtime, parser, RAG, collector, snapshot, or publication behavior | powershell -ExecutionPolicy Bypass -File .\run_tests.ps1 |
| Docker packaging | docker build -t clash-royale-agent . and compose config checks |
| Live provider or Supercell behavior | Manual credentialed smoke from an allowlisted environment |

Public CI must remain deterministic. It must not require private snapshots,
real model calls, Supercell access, local player data, or Windows-only
environment variables during module import.

Before review, run the composed repository gate:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_repo.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\check_repo.ps1 -Full
~~~

## Commit Discipline

- Prefer small commits that can be reviewed and reverted independently.
- Use descriptive messages such as docs: slim readme and split operations guides
  or fix: make supervisor tests portable on CI.
- Keep generated files and private runtime state out of staged changes.
- Stage explicit reviewed paths; do not bulk-stage the repository.
- Review staged diffs before committing:

~~~powershell
git status --short
git diff --staged --check
git diff --staged
~~~

Before pushing, scan the staged path list for common private-data patterns:

~~~powershell
git diff --cached --name-only |
  Select-String -Pattern '^data/|^logs/|^tmp/|\.env|\.sqlite|\.sqlite3|\.db|\.jsonl|\.zip|trace|benchmark|\.key|\.pem'
~~~

The scan should have no output except intentionally reviewed public fixtures,
such as data/card_aliases.zh-CN.json, evaluation/cases.jsonl, or
evaluation/fault_scenarios.jsonl.

## Pull Request Checklist

- [ ] The change has one clear purpose.
- [ ] Public tests or the relevant smaller gate were run.
- [ ] No private data, generated reports, tokens, player tags, or raw traces are staged.
- [ ] Docs were updated for public APIs, RAG/SSE behavior, collection behavior, or operations changes.
- [ ] New operational surfaces are private, protected, or clearly documented.
- [ ] Windows-only tests skip safely on non-Windows hosts before touching Windows-only environment variables.
- [ ] License changes stay consistent with Apache-2.0 unless the project owner chooses a new license.

## Where to Document Changes

- Architecture or module boundaries: docs/ARCHITECTURE.md
- Operations and deployment boundaries: docs/OPERATIONS.md
- Data scopes, identifiers, snapshots, fact levels, or reporting vocabulary: docs/DATA_CONTRACT.md
- Structured QA, RAG, SSE, evidence, and timeout semantics: docs/RAG_AND_QA.md
- Test layers, counts, CI rules, and report handling: docs/TESTING.md
- Package layout and module moves: docs/PACKAGE_MIGRATION.md
- Significant decisions: a new ADR under docs/decisions/
- Cleanup sequencing: docs/REPO_HEALTH_PLAN.md
