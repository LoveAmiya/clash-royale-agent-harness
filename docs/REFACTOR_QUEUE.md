# Remaining Refactor Queue



This queue is the continuation ledger for the repository cleanup. It covers

unfinished Phase 2, Phase 4, Phase 5, Phase 6, plus earlier gaps that should be

closed before the project is considered healthy.



## Operating Rules



- Work one small behavior-preserving slice at a time.

- If a row is marked `doing`, continue that row before starting a new one.

- If no row is `doing`, pick the first `todo` row.

- When starting a row, change its status to `doing` in this file.

- When the row is complete and verified, change its status to `done` and add a

  short evidence note.

- If the slice is blocked by credentials, live Supercell access, a running

  collector, or missing user authority, mark `blocked` with the exact reason.

- If a slice finishes early, continue with the next row in the same run.

- Do not commit, push, bulk-stage, or touch private runtime data.

- Do not change collection strategy, RAG behavior, public API contracts, or

  scheduled-task behavior unless that row explicitly says so.



## Standard Verification



- Docs-only slice: `powershell -ExecutionPolicy Bypass -File .\scripts\check_repo.ps1 -SkipTests`

- Import/package slice: `.\.venv\Scripts\python.exe -m unittest discover -s tests`

- Runtime, QA/RAG, collector, snapshot, or publication slice: `powershell -ExecutionPolicy Bypass -File .\run_tests.ps1`

- Always inspect `git status --short` before finishing. Data, logs, tokens,

  SQLite, JSONL traces, generated reports, player tags, and raw battle artifacts

  must not be staged or newly tracked.



## Approximate Count



Current plan: about 39 small slices. The count may shrink if adjacent docs-only

items are safely finished together, or grow if a monolith split exposes hidden

contracts that need a separate regression test.



## Queue



| # | Phase | Status | Slice | Evidence / notes |

|---:|---|---|---|---|

| 1 | Earlier gap | done | Run a fresh repo health audit: current `git status`, ignored private outputs, oversized modules, entry commands, and current test baseline; update this queue if the facts changed. | 2026-08-17 heartbeat audit: private outputs remained ignored; six root monoliths still 1280-2043 lines; entry scripts present; `scripts/check_repo.ps1 -SkipRuff` passed with 1011 tests, 1 skipped. |

| 2 | Earlier gap | done | Audit all root entry points that import packaged modules without first loading `app_config`; add focused tests for any remaining direct-entry import failures. | Added `tests/test_entrypoint_package_bootstrap.py`; targeted `test_entrypoint_package_bootstrap` and `test_evaluation_entrypoints` passed. |

| 3 | Earlier gap | done | Add a dev-tooling dependency path, such as `requirements-dev.txt`, for Ruff/pytest/mypy without making CI depend on live providers. | Added `requirements-dev.txt`; `test_repo_tooling_config`, `test_entrypoint_package_bootstrap`, and `test_evaluation_entrypoints` passed. |

| 4 | Earlier gap | done | Make `scripts/check_repo.ps1` report a concise summary of checks run, skipped advisory tools, and private-path scan results. | Added check-run, skipped-tool, and private-path summaries; `test_check_repo_script`, tooling/bootstrap tests, and `check_repo.ps1 -SkipTests -SkipRuff` passed. |

| 5 | Phase 2 | done | Extract the remaining runtime startup/lifespan orchestration from `runtime_multi.py` into `clashroyale_agent.api.startup` or lifecycle helpers, preserving startup behavior. | Added `initialize_runtime_services` for metrics, feedback, quota creation/probe; lifecycle/package tests and `check_repo.ps1 -SkipRuff` passed with 1014 tests, 1 skipped. |

| 6 | Phase 2 | done | Extract RAG preheat orchestration from `runtime_multi.py` into an API preheat/lifecycle module with targeted regression tests. | Added `clashroyale_agent.api.rag_preheat` with explicit dependency bundle; RAG preheat/API package/production tests and `check_repo.ps1 -SkipRuff` passed with 1014 tests, 1 skipped. |

| 7 | Phase 2 | done | Extract live snapshot refresh orchestration from `runtime_multi.py` into a snapshot lifecycle module without changing cooldown or publication behavior. | Added `api.snapshot_lifecycle` with explicit runtime dependencies; cache, incomplete/source-exhausted cooldown, publication, RAG activation, follower, and one-shot refresh remain behind compatibility facades. Package/snapshot/deployment tests and `run_tests.ps1` passed: 1015 tests, 1 skipped. |

| 8 | Phase 2 | done | Extract dataset dependency resolution and catalog route support from `runtime_multi.py`, keeping dataset-scope responses identical. | Added `api.dataset_runtime` with explicit dataset/RAG/repository dependencies; catalog, structured repository, and rolling retriever keep root compatibility facades. Package/dataset/structured API tests and `run_tests.ps1` passed: 1016 tests, 1 skipped. |

| 9 | Phase 2 | done | Extract remaining health/readiness/model-status/metrics route registration and dependency wiring from `runtime_multi.py`. | Added `api.status_runtime` with deferred configuration providers so public route payloads retain request-time runtime reads. Status/quality/production tests and `run_tests.ps1` passed: 1017 tests, 1 skipped. |

| 10 | Phase 2 | done | Extract process/SSE route handler body from `runtime_multi.py` into `clashroyale_agent.api.process_routes`, preserving event order and payloads. | Added `ProcessRuntimeDependencies` and `handle_process_request`; root process entry remains a request-time compatibility facade, including the historical `split_stream_chunks` export. Process/SSE, disconnect, quota, package-boundary tests and `run_tests.ps1` passed: 1018 tests, 1 skipped. |

| 11 | Phase 2 | done | Reduce API app creation and route registration in `runtime_multi.py` to compatibility wiring plus dependency assembly; keep `run_api.ps1` unchanged. | Added `api.runtime` with `RuntimeAppDependencies` and centralized non-process route registration. Root runtime supplies request-time callbacks and keeps direct compatibility symbols pending QA slices #12-20; `run_api.ps1`/`run_backend.ps1` are unchanged. Route tests and `run_tests.ps1` passed: 1019 tests, 1 skipped. |

| 12 | Phase 2 | done | Split `query_parser.py` schema/constants into package modules under `clashroyale_agent.qa`, preserving fallback output contracts. | Added `qa.parser_schema` for the parser prompt, confidence levels, tower entities, and multi-intent cap; `query_parser` retains compatibility exports. Parser intent/confidence/multi-intent tests and `run_tests.ps1` passed: 1020 tests, 1 skipped. |

| 13 | Phase 2 | done | Split `query_parser.py` local normalization and alias helpers into package modules with focused parser regression tests. | Added `qa.card_aliases.CardAliasResolver` for normalization, cached alias construction, boundary-aware matching, and ordered resolution; root parser retains compatibility bindings and alias data ownership. Alias/parser/multi-intent tests and `run_tests.ps1` passed: 1021 tests, 1 skipped. |

| 14 | Phase 2 | done | Split `query_parser.py` fallback parser orchestration from model-parser orchestration, preserving timeout and fallback semantics. | Added `qa.parser_orchestration` with explicit local-parser/model dependencies; `runtime_multi.parse_user_query` remains a compatibility facade, including its model-call patch point. Timeout, invalid-response, reconciliation, and validated-result paths are preserved. Targeted tests and `run_tests.ps1` passed: 1022 tests, 1 skipped. |

| 15 | Phase 2 | done | Reduce `query_parser.py` to a compatibility wrapper once packaged parser modules own the implementation. | 2026-08-18 heartbeat: added `qa.parser_fallback` and `qa.parser_multi_intent`; root `query_parser.py` is 540 lines and now delegates fallback parse assembly, multi-intent decomposition, and multi-intent normalization through explicit dependency bundles. Targeted parser tests passed; `run_tests.ps1` passed with 1032 tests, 1 skipped. |

| 16 | Phase 2 | done | Split `query_answering.py` intent routing into `clashroyale_agent.qa` without changing direct-answer behavior. | 2026-08-18 heartbeat: added `qa.answer_routing` for RAG routing predicates, subquery titles, and subquery user-text normalization; `query_answering.py` and `runtime_multi.py` keep compatibility entry points as thin delegators. Targeted routing/query/multi-intent/open-analysis tests passed; `run_tests.ps1` passed with 1039 tests, 1 skipped. |

| 17 | Phase 2 | done | Split `query_answering.py` structured-answer orchestration from RAG orchestration with contract tests. | 2026-08-18: added `qa.structured_answering` with injected planner/executor dependencies and updated the root `answer_query` single-intent path to delegate through it while keeping fallback text and metadata contracts. Targeted structured/routing/query/open-analysis tests passed; `run_tests.ps1` passed with 1041 tests, 1 skipped. |

| 18 | Phase 2 | done | Split `query_answering.py` evidence ledger and grounding validation into a package module. | 2026-08-18 heartbeat: evidence ledger, reference suffixing, grounded stream buffer creation, completed-answer filtering, and ledger grounding validation live in `qa.evidence_grounding`; targeted evidence/streaming/synthesis tests passed. |

| 19 | Phase 2 | done | Split `query_answering.py` synthesis fallback and streaming presentation into package modules. | 2026-08-18 heartbeat: added `qa.synthesis_fallbacks` for snapshot/retrieved-evidence fallbacks and `qa.presentation` for stable chunked content emission; root imports keep compatibility. Targeted QA tests passed; `run_tests.ps1` passed with 1048 tests, 1 skipped. |

| 20 | Phase 2 | done | Reduce `query_answering.py` to a compatibility wrapper after packaged QA modules own the implementation. | 2026-08-18 heartbeat: `query_answering.py` is now 517 lines and delegates RAG answering to `qa.rag_answering`, evidence synthesis to `qa.evidence_synthesis`, multi-intent execution/composition to `qa.multi_intent_answering`, plus existing reviewer/retrieval/trace/structured-answer package modules. Root compatibility patch points are preserved for historical tests. Targeted QA/RAG tests passed; `run_tests.ps1` passed with 1056 tests, 1 skipped. |

| 21 | Phase 2 | done | Split `supercell_live.py` API client and request/rate/preflight helpers into `clashroyale_agent.collection`. | 2026-08-18 heartbeat: added `collection.api_client.OfficialAPIRequester` for bearer auth, timeout, pacing, Retry-After cooldown, JSON validation, metrics, and legacy `_get_json`; `SupercellAPIClient` now inherits it while preserving `supercell_live.requests.Session` patch semantics. Package/client tests and `test_supercell_live_data` passed; `run_tests.ps1` passed with 1060 tests, 1 skipped. |

| 22 | Phase 2 | done | Split `supercell_live.py` battle parsing into a collection parser module with synthetic fixtures only. | 2026-08-18 heartbeat: added `collection.battle_parser` for Path of Legend filtering, normalized battle records, opponent tag expansion, and usable-battle selection; root `supercell_live.py` keeps compatibility exports. Targeted collection/Supercell tests passed; `run_tests.ps1` passed with 1062 tests, 1 skipped. |

| 23 | Phase 2 | done | Split `supercell_live.py` deck/loadout normalization into a collection normalization module. | 2026-08-18 heartbeat: added `collection.loadout_normalization` for loadout card/side normalization, canonical loadouts, signatures, payloads, and quality scoring; root `battle_loadout.py` remains a compatibility wrapper with `app_config` path bootstrap. Targeted collection/Supercell/rolling-corpus tests passed; `run_tests.ps1` passed with 1063 tests, 1 skipped. |

| 24 | Phase 2 | done | Move rolling corpus/materializer implementation behind `clashroyale_agent.collection` modules while keeping scripts as thin entry points. | 2026-08-18 heartbeat: root `rolling_corpus.py`, `rolling_materializer.py`, and `scripts/collect_rolling_corpus.py` are compatibility entry points for packaged collection modules; added `collection.rolling_collector` and preserved legacy patch/import identities. Targeted collection/rolling tests passed: 49 tests. |

| 25 | Phase 2 | done | Verify collector script and Windows scheduled-task entry compatibility by text-level tests only; do not restart or modify live tasks. | 2026-08-18 heartbeat: added text-level coverage that the legacy collector script is only a packaged entry point and that Windows runners/installers still target the legacy script and existing runner chain. Targeted entry/schedule tests passed (56 tests); full public gate passed (1067 tests, 1 skipped). |

| 26 | Phase 2 / 4 | done | Move `web_ui_template.py` content into `src/clashroyale_agent/web/templates` and/or `static` without changing rendered UI. | 2026-08-18 heartbeat: moved the rendered HTML into `web/templates/index.html`; `web.template_loader` owns the file-backed `HTML_PAGE` and root `web_ui_template.py` is a compatibility module alias. Template identity and visualization tests passed; full unittest discovery passed with 1070 tests, 1 skipped. |

| 27 | Phase 2 | done | Split `web_app.py` API proxy, SSE rendering, static/template serving, and app startup into package modules. | 2026-08-18 heartbeat: packaged schemas, HTTP proxy helpers, SSE payload/forwarding, template loader, and app runtime now own those concerns; root `web_app.py` remains a 269-line route compatibility surface. Added contract tests for models, proxy status/envelopes, SSE payloads, and runtime creation. `run_tests.ps1` passed: 1079 tests, 1 skipped. |

| 28 | Phase 4 | done | Final README slimming pass: keep only project purpose, quick start, core links, data boundary, and high-level layout. | 2026-08-18 heartbeat: reduced README to project purpose, core capabilities, quick start, local launch, data boundary, high-level layout, documentation ownership links, and license. Removed duplicate API/testing/deployment/runbook detail. `check_repo.ps1 -SkipTests -SkipRuff` passed with 0 tracked private/generated paths. |

| 29 | Phase 4 | done | Deduplicate `00_START_HERE.md`, `docs/OPERATIONS.md`, and collection handoff docs so startup, recovery, and operations each have one owner. | 2026-08-18 heartbeat: `00_START_HERE.md` now owns only local API/Web startup and safe restart; `OPERATIONS.md` owns environment, exposure, deployment, host and troubleshooting boundaries; `SNAPSHOT_COLLECTION_HANDOFF.md` remains the sole collector task/recovery/PushPlus command owner. `check_repo.ps1 -SkipTests` passed with 0 tracked private/generated paths (Ruff advisory skipped because unavailable). |

| 30 | Phase 4 | done | Align `docs/ARCHITECTURE.md`, `docs/DATA_CONTRACT.md`, and `docs/RAG_AND_QA.md` with the actual package boundaries after each completed split. | 2026-08-18 heartbeat: documented package ownership for API, Web, QA, collection, stats, snapshots and ops while retaining root compatibility-entry boundaries and unchanged public contracts. `check_repo.ps1 -SkipTests` passed with 0 tracked private/generated paths (Ruff advisory skipped because unavailable). |

| 31 | Phase 4 | done | Add or update ADR index entries for package migration, private data boundaries, quality gates, and behavior-preserving refactor policy. | 2026-08-18 heartbeat: added ADR-015 covering package ownership, compatibility facades, private/generated data boundaries, deterministic gates, document ownership, and candidate-first cleanup; indexed and linked it from the ADR guide. `check_repo.ps1 -SkipTests` passed with 0 tracked private/generated paths (Ruff advisory skipped because unavailable). |

| 32 | Phase 5 | done | Produce `docs/reports/dead_code_candidates.md` from static references, docs references, scripts, and entry-point names; classify delete/confirm/keep. | 2026-08-18 heartbeat: added a static-reference audit with explicit removable/confirm/keep classifications. No tracked output or business file is unambiguously safe to delete; collector and compatibility boundaries are retained. `check_repo.ps1 -SkipTests` passed with 0 tracked private/generated paths (Ruff advisory skipped because unavailable). |

| 33 | Phase 5 | blocked | Clean one confirmed dead-output theme only, after candidate review; keep business code and collector compatibility untouched. | Blocked 2026-08-18: the candidate audit found no owner-approved removable theme. `run_collector.ps1`, `client.py`, legacy schedule helpers and migration tools require external-automation/operator confirmation before deletion. |

| 34 | Phase 6 | done | Add `evaluation/reports/README.md` and document generated reports, benchmark commands, and no-commit rules. | 2026-08-18 heartbeat: added report classes, deterministic/live command guidance, retention/redaction rules, and no-commit checks; adjusted `.gitignore` to track only this README while ignoring generated children. `check_repo.ps1 -SkipTests` passed with 0 tracked private/generated paths. |

| 35 | Phase 6 | done | Standardize RAG benchmark reporting for retrieval latency, candidate count, and hit-rate fields. | 2026-08-18 heartbeat: retained legacy `latency_ms` and `metrics`, added `retrieval_latency_ms`, candidate-count summary, and `hit_rate_at_k` aliases per variant. Contract test added; `run_tests.ps1` passed with 1079 tests, 1 skipped. |

| 36 | Phase 6 | done | Standardize QA latency reporting for first-token time, total time, timeout rate, and fallback rate. | 2026-08-18 heartbeat: added explicit `timeout_rate` and `fallback_rate` scorecard fields, preserved first/total latency, and normalized those fields from QA result rows without retaining question text. Added 2 contract tests; `run_tests.ps1` passed with 1080 tests, 1 skipped. |

| 37 | Phase 6 | done | Standardize collector batch baseline reporting for batch duration, pre/post dedupe increments, and staging size. | 2026-08-18 heartbeat: added additive `batch_baseline` to collector results with total duration, raw/post-dedupe/inserted increments, and staging size/limit; retained all existing result fields. Contract test added; `run_tests.ps1` passed with 1081 tests, 1 skipped. |

| 38 | Phase 6 | done | Standardize API startup and RAG preheat baseline reporting, then link the metrics from operations docs. | 2026-08-18 heartbeat: added API startup and latest RAG preheat elapsed/outcome baselines to `/health` and documented their safe aggregate contract in `docs/OPERATIONS.md`; startup/preheat/status tests added. `run_tests.ps1` passed with 1083 tests, 1 skipped. |

| 39 | Final review | done | Run full public gate, inspect private-data status, update docs with final remaining risks, and summarize next phase if any. | 2026-08-18 heartbeat: full public gate passed with 1083 tests, 1 skipped; tracked private/generated path scan remains 0 and worktree status contains only source/docs/test/config paths. Health plan now records remaining oversize facades, advisory tooling, blocked candidate cleanup, aggregate-only baselines, and pre-existing CRLF whitespace found by the optional unstaged scan. |

