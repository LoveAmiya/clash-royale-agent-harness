# ADR-015: Repository Health and Compatible Package Migration

## Status

Accepted

## Date

2026-08-18

## Context

The project accumulated large root modules, mixed documentation ownership, and
generated local artifacts alongside public source. These conditions make a
behavioral regression difficult to distinguish from a move-only refactor and
increase the chance of exposing private operational data.

The API, QA/RAG, collection, snapshot, web, and operations responsibilities
need package owners while existing PowerShell commands, scheduled-task targets,
root imports, public API responses, and SSE payloads remain in use.

## Decision

1. Put new implementation behind `src/clashroyale_agent/` package owners and
   retain root modules/scripts as explicit compatibility facades until their
   callers migrate.
2. Split one responsibility at a time. A move/split must not change collection
   strategy, RAG policy, public API contracts, SSE contracts, scheduled-task
   behavior, or private-data handling unless a dedicated decision and tests
   authorize that behavioral change.
3. Keep `data/`, logs, temporary state, SQLite databases, JSONL traces,
   generated reports, archives, raw battles, player identifiers, credentials,
   and tokens out of source control. Reports are local evidence, not commits.
4. Require a targeted contract test for each touched boundary, then run the
   appropriate deterministic gate before closing the slice. Live providers are
   opt-in and must be mocked or skipped in the default gate.
5. Give each operational document one owner: startup, operations, collection
   recovery, contracts, testing, and decisions link to each other instead of
   duplicating commands or historical test counts.
6. Track work in a resumable queue with small reviewable slices. Dead-code
   removal starts with a candidate report and is never bundled with a behavior
   migration.

## Consequences

- Existing user commands and scheduled collectors continue to resolve their
  historical root entry points during migration.
- Package tests can assert identity and output contracts while facades preserve
  external imports.
- Review diffs stay attributable to one concern: documentation, package move,
  compatibility, cleanup, or baseline instrumentation.
- Git ignore rules and repository checks become part of the delivery boundary,
  but human review remains necessary because path checks cannot prove content
  is safe.

## Verification

- `docs/REPO_HEALTH_PLAN.md` records the target layout and non-goals.
- `docs/REFACTOR_QUEUE.md` records the active slice, evidence, and next work.
- `scripts/check_repo.ps1` scans tracked/staged paths and runs static checks.
- `run_tests.ps1` remains the public deterministic behavior gate; credentialed
  Supercell, model, and live smoke checks remain opt-in.
