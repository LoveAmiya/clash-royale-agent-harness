# Dead-Code Candidate Audit

Date: 2026-08-18

This is a static-reference audit, not deletion authority. It searched root
Python module stems, scripts, documentation and tests with `rg`, while excluding
private runtime paths. Static search cannot see Windows scheduled tasks, a
manual PowerShell invocation, external automation, or a downstream import.

No code, scripts, runtime data, logs, or generated reports were deleted during
this audit.

## Confirmed Removable

None. The repository has no tracked generated output or unambiguously orphaned
business file that is safe to remove without an owner review.

Ignored runtime outputs such as logs, SQLite, JSONL traces, archives and
evaluation reports are not candidates for this change. Their retention or
cleanup is an operations action and must not be mixed with code deletion.

## Needs Owner Confirmation

| Candidate | Static evidence | Why confirmation is required |
|---|---|---|
| `run_collector.ps1` | No in-repository filename reference was found. It starts an older isolated collector server through `run_backend.ps1`. | It may be an operator's manual entry point or an external scheduled task; verify task definitions and operator usage before removal. |
| `client.py` | Removed 2026-08-19 after a repository-wide reference scan found no import, documented command, or scheduled-task reference. | Keep the deletion isolated; restore only if an operator later identifies an external dependency. |
| `run_rolling_collection.ps1`, `scripts/run_rolling_schedule.ps1`, `scripts/install_rolling_schedule.ps1` | They form an older rolling-schedule chain and are referenced by historical plan docs/tests, while current collection uses the parallel-task installer. | Preserve until Windows task inventory and backward-compatibility intent are reviewed. |
| `scripts/monitor_snapshot.ps1`, `scripts/migrate_legacy_snapshot.py`, `scripts/import_snapshot_review.py` | Historical recovery and migration docs still provide runnable commands for these files. | Retain as explicit recovery compatibility tools; do not include them in dead-code deletion until those runbooks are retired and their private-data workflows are migrated. |

## Keep

| Item group | Evidence | Reason |
|---|---|---|
| Root compatibility modules: `runtime_multi.py`, `query_parser.py`, `query_answering.py`, `supercell_live.py`, `rolling_corpus.py`, `rolling_materializer.py`, `web_app.py`, `web_ui_template.py` | Referenced by entry scripts, tests, package-boundary tests, or documented commands. | They preserve historical imports and PowerShell command behavior during package migration. |
| Root QA/retrieval helpers: `analysis_boundaries.py`, `answer_builder.py`, `answer_presentation.py`, `deck_archetypes.py`, `rag_document_policy.py`, `rag_data_builder.py`, `rag_quality.py`, `retrieval_postprocess.py` | Direct Python imports from runtime/package modules, skills, evaluations or tests. | They still provide active behavior and require a dedicated package-migration slice before any removal. |
| Collector scripts: `scripts/collect_rolling_corpus.py`, `scripts/run_daily_ranked_schedule.ps1`, `scripts/run_daily_ranked_supervisor.ps1`, `scripts/install_parallel_collection_tasks.ps1` | Current runner and installer tests assert these names and their invocation chain. | They are live compatibility surfaces; do not restart, rename, or delete them in cleanup work. |
| Public entry scripts: `run_api.ps1`, `run_web.ps1`, `run_tests.ps1`, `run_backend.ps1` | README/runbook references and entry-point tests. | These are supported operator interfaces. |

## Follow-up Rules

1. Review one `Needs Owner Confirmation` item at a time.
2. Verify scheduled-task and external automation ownership without changing it.
3. Make any approved deletion a cleanup-only slice with a targeted regression
   test and an updated document reference.
4. Re-run `scripts/check_repo.ps1` and inspect `git status --short` after each
   deletion; never add runtime artifacts as evidence.
