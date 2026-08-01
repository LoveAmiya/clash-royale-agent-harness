# Clash Royale Meta QA Without Clan-War Agent

Status: Active implementation
Date: 2026-07-28
Target branch: `feature/meta-qa-no-clan-war`

## Objective

Build a high-end Clash Royale data QA system that answers most card, deck, matchup, and meta-analysis questions from official Supercell API battle-log evidence. Remove clan-war schedule lookup and clan-war preparation advice, while preserving the existing free-form QA, multi-intent parsing, advanced RAG, traceability, model safety, feedback, monitoring, and snapshot quality gates.

The product should expose two complementary paths:

- Structured tools for deterministic statistics: single-card stats, card comparison, exact deck profile, exact deck-vs-deck matchup, and meta tables.
- Free-form analysis for high-level questions: natural-language parsing, multi-intent decomposition, snapshot-scoped RAG retrieval, evidence compression/reranking, and model synthesis grounded in retrieved evidence.

## Implementation Progress

Implemented on `feature/meta-qa-no-clan-war`:

- Independent feature branch and accepted ADR.
- Clan-war schedule, schedule-summary, and preparation intents now route to `UNSUPPORTED_CLAN_WAR_FEATURE`; historical schedule code/data remains intact but unregistered.
- Free-form parsing, multi-intent execution, environment RAG synthesis, trace/SSE, safety, feedback, and observability remain active.
- Weekly target is 200,000 battles with ordered concurrency `1`, default `1` request/second, 30-second request timeout, 8-hour collection budget, and 24-hour hard cap.
- Collection is closed over Path of Legend: global PoL top 1,000 seeds, then opponent-tag expansion only from accepted `pathOfLegend` battles. Non-PoL records are discarded before deduplication and queue expansion.
- The application model contract is fixed to the user-selected OpenAI-compatible relay at `https://crs.ruinique.com`, Responses API, `gpt-5.5`, and `medium`; only `OPENAI_API_KEY` is read from the environment.
- Snapshot archives retain the current and previous complete package only; retention metadata is exposed in `/snapshot/status`.
- Collection progress is published hourly and `scripts/monitor_snapshot.ps1` records it locally without model calls.
- Official payloads now produce a tower/evolution/elite field-presence probe while the active deck contract remains ordinary 8-card mode.
- Collection is disk-backed and resumable: normalized battles and exact deck/matchup aggregates are committed per player to `data/snapshot_work/collection-*` instead of accumulating API payloads in RAM.
- Snapshot publication streams JSONL into the canonical and archive JSON files, preserves the complete exact aggregate SQLite store, and removes the work directory only after the canonical snapshot is atomically published.
- Collector restart reads `official_snapshot_pointer.json` plus the archive's compact collector summary; it never parses the canonical 200,000-record raw array merely to restore status.
- Deterministic audit export and isolated external-review import are implemented. Audit files are hashed locally; reviewed RAG text is staged only after document coverage, immutable metadata/source fields, and numeric claims pass validation. Import never replaces active RAG documents.
- A snapshot-scoped structured SQLite builder expands only valid exact-8-card battles into both side perspectives and derives card, teammate, opponent, deck, exact-matchup, and heuristic archetype indexes without model calls.
- Read-only structured APIs now expose the card catalog, single-card stats, two-card comparison, exact deck profile, exact deck matchup, and archetype environment with a consistent error envelope and snapshot provenance.

Pending product review:

- Replace the first-pass heuristic archetype taxonomy after the user audits its rules and representative decks. Do not silently rename or merge categories before that review.

## Scope Boundaries

### Keep

- Free-form web QA and the existing natural-language parser.
- Multi-intent decomposition and routing for non-clan-war questions.
- Advanced RAG over snapshot-derived evidence documents.
- Local BM25 plus local Ollama/Qdrant vector retrieval.
- Model gateway, circuit breaker, streaming/SSE, request trace, feedback store, metrics, readiness checks, strict external-data mode, and snapshot/RAG fingerprint alignment.
- Existing schedule files and old schedule skills as historical/inactive code until deletion is explicitly approved.

### Remove From User-Facing Product

- Clan-war schedule lookup.
- Clan-war preparation advice.
- UI examples and evaluation cases that imply the system still supports clan-war scheduling or clan-war prep.

### First Version Limits

- Support exact 8-card decks only.
- Do not require tower troop, evolution, or elite information.
- Add a battle-log field probe for tower/evolution/elite data. If official fields are missing or unstable, keep the UI and answers in 8-card mode and explicitly state that tower/evolution/elite data is not included.
- Do not generate specific in-game play-by-play tactics. Answers should focus on usage, win rate, net win rate, sample size, matchup evidence, deck positioning, meta environment, and data confidence.

## Data Pipeline

### Token And API Cost Guardrails

These are project-wide acceptance constraints, not optional optimizations:

- Collection, normalization, deduplication, aggregation, slicing, validation, hashing, retention, and audit export must make zero cloud LLM or cloud embedding calls.
- Snapshot monitoring must use only the local `/snapshot/status` endpoint. The default monitor interval is one hour and the monitor writes compact local JSONL; it must never initialize the parser, answer model, retriever, or embedding model.
- RAG documents remain high-density deterministic aggregates. Raw battles must never become one-vector-per-battle documents.
- Embeddings use local Ollama (`bge-m3`) and indexes use local Qdrant by default. A future cloud embedding backend requires an explicit configuration change and a documented cost estimate.
- Structured card, comparison, deck, matchup, and table endpoints must not call a model. Cloud model calls are limited to parser fallback and final synthesis on the free-form RAG page.
- Data collection, scope validation, audit sampling, aggregation, RAG slicing, hashing, and structured-index builds must never use the project model key. They are deterministic local jobs even at 200,000 records.
- Automated snapshot verification is deterministic and local. Optional manual review through ChatGPT web uses isolated audit exports and does not use the project's `OPENAI_API_KEY`.
- Every new pipeline stage must document whether it performs Supercell requests, local embedding work, or cloud model calls. Tests should fail if a structured handler unexpectedly reaches the model gateway.
- Collector memory must be bounded by the ranking candidate list, the current request batch, the fixed card universe, and capped published summaries. Raw battles, unique exact decks, and exact matchups must be stored on disk and must not be accumulated in Python lists or unbounded dictionaries.
- The collector defaults to strict rank order, concurrency `1`, and `1` request per second. Rate-limit responses invalidate the candidate snapshot and preserve the previous complete snapshot.
- Zero-cost dynamic-IP operation is supported, but collection must pass `supercell_preflight` before the collector starts. The preflight reads the current public egress IP, decodes the Supercell token client CIDR allowlist without printing the token, skips the official probe when the IP is not allowlisted, and sends at most one minimal official ranking probe when the IP matches.
- If the public IP changes, prefer updating the existing Supercell token allowlist when possible. Creating a new API key weekly is acceptable as an operational fallback for this project, but should not be used to bypass official limits, increase concurrency, share credentials, or rotate leaked keys without revocation. The collector startup uses the persisted Windows user token in collector mode so newly generated weekly keys are picked up without restarting Codex.

Operational defaults:

- Per-request Supercell timeout: 30 seconds.
- Full collection budget: 8 hours by default, configurable up to 24 hours.
- In-process progress publication: once per hour plus one final update.
- External low-token monitor: `scripts/monitor_snapshot.ps1`, default once per hour, writing `logs/snapshot-monitor.jsonl`.
- Manual preflight command: `powershell -ExecutionPolicy Bypass -File .\scripts\preflight_supercell.ps1 -PreferUserToken`.

Memory and restart invariants:

- The production collector always supplies `data/snapshot_work` as its spool root.
- Collector startup restores only the compact active snapshot summary. Full canonical JSON loading is reserved for API/RAG processes.
- Each completed player is one SQLite transaction. After a process or machine failure, the next run reuses the saved leaderboard candidate list and resumes after the last committed player.
- An incomplete work area is resumable for at most 14 days and is never treated as a published snapshot.
- At concurrency `1` and 25 battle-log entries per player, no more than 25 battle records are intentionally retained by the collection layer at once.
- All exact matchups remain in the snapshot archive's `aggregates.sqlite`. The in-memory/JSON matchup summary is capped at 20,000 highest-sample rows; truncation counts are explicit in `collection_metrics`.
- A synthetic 200,000-record, 8v8 local benchmark on 2026-07-28 completed in 116.16 seconds with RSS growth of 4.2 MB, Python allocation peak of 0.7 MB, SQLite size of 85.5 MB, and JSONL size of 69.7 MB. This proves bounded collector growth for the tested shape; real API payload size and disk size may be larger.
- Collection and stream publication perform zero LLM, embedding, or vector-database calls.

### Weekly Snapshot

- Replace the daily 20,000-battle production target with a weekly 200,000-battle target.
- Keep the target fixed at 200,000; 300,000 is outside the current plan.
- Fetch initial seeds only from `/locations/global/pathoflegend/players`, capped at the first 1,000. Use the 12,000-player setting only as the bounded queue capacity for PoL opponent expansion.
- Accept only official battle-log records whose `type` is `pathOfLegend`. Rejected records must increment a local metric but must not affect battle counts, aggregates, deduplication, or queued tags.
- Continue rejecting incomplete, timed-out, rate-limited, or validation-failing snapshots. A failed refresh must not replace the last complete snapshot.

Expected runtime based on current observation:

- 20,000 battles: about 30 minutes.
- 200,000 battles: about 300 minutes linearly, planned as 5 to 8 hours after allowing for duplicate logs, retries, cooldown, disk writes, and index generation.

### Current Archetype Audit Boundary

- The current classifier is a deterministic first-match card-presence heuristic, not a learned model or an official taxonomy.
- Priority currently runs from Electro Giant and Hog/Hog EQ through Lava, Golem, PEKKA, bait, siege, Royal/Goblin Giant, Graveyard, Balloon, Drill, Miner, and Giant.
- `Unclassified deck family` means none of those trigger cards matched. The current legacy mixed-mode snapshot contains many recognizable Royal Hogs/Recruits, Three Musketeers, Ram Rider, Mega Knight/Wall Breakers, Elixir Golem, and Battle Ram families there.
- Do not change this taxonomy until the user reviews representative decks. The next iteration should add explicit families, reduce unclassified coverage, and keep first-match priority auditable.

### Raw And Audit Exports

Separate runtime data from audit/export data so external review can happen without consuming the project API key.

Runtime data:

- `data/official_daily_snapshot.json` (compatibility filename for the weekly canonical snapshot)
- `data/rag_documents.json`
- `data/daily_snapshot_qdrant/{snapshot_id}/`
- `data/snapshot_archives/{snapshot_id}/aggregates.sqlite` (complete exact deck/matchup aggregate store)

Audit/export data:

- `data/audit_exports/{snapshot_id}/manifest.json`
- `data/audit_exports/{snapshot_id}/raw_battlelogs.part-*.jsonl`
- `data/audit_exports/{snapshot_id}/normalized_battles.jsonl`
- `data/audit_exports/{snapshot_id}/side_records.jsonl`
- `data/audit_exports/{snapshot_id}/cards.csv`
- `data/audit_exports/{snapshot_id}/decks.csv`
- `data/audit_exports/{snapshot_id}/matchups.csv`
- `data/audit_exports/{snapshot_id}/archetypes.csv`
- `data/audit_exports/{snapshot_id}/rag_documents.generated.jsonl`
- `data/audit_exports/{snapshot_id}/README_FOR_CHATGPT.md`

The collector retains the complete normalized corpus but only a bounded raw API probe subset. Therefore `raw_battlelogs.part-*.jsonl` is explicitly labeled as probe-only in the manifest; `normalized_battles.jsonl` plus its partitions contain the complete 200,000-record review corpus. This boundary must not be relabeled as complete raw API payload retention.

The first 200,000-battle snapshot contained 1,705 records where at least one side was not a complete 8-card deck. Audit export preserves and counts them. Phase 5 exact-deck statistics must filter to records with exactly 8 cards on both sides and expose the excluded count in provenance.

The audit package is meant to be uploaded manually to ChatGPT web for review or alternate slicing. That review path should not use the project `OPENAI_API_KEY`. If ChatGPT web returns reviewed slices, import them under:

- `data/external_reviews/{snapshot_id}/rag_documents.reviewed.jsonl`
- `data/external_reviews/{snapshot_id}/review_notes.md`

The project must validate imported review output locally before activation: `snapshot_id`, raw file hashes, document IDs, metadata, document fingerprint, source fields, and numeric agreement with the structured snapshot.

Local commands:

- `python scripts/export_snapshot_audit.py`
- `python scripts/import_snapshot_review.py <snapshot_id> <rag_documents.reviewed.jsonl> --review-notes <review_notes.md>`

Review import currently stages validated material under `data/external_reviews/`; it deliberately does not activate or replace generated RAG evidence until the open replacement-versus-additional-source decision is resolved.

### Retention

Keep exactly the practical minimum:

- Retain at most 14 days of completed snapshots.
- Retain at most 2 complete snapshots: current active snapshot and one previous rollback snapshot.
- If those rules conflict, preserve current active plus the previous complete rollback snapshot.
- Cleanup runs only after a new snapshot, RAG documents, local index, and validation reports are fully published and aligned.
- Cleanup includes old snapshot archives, old RAG docs, local Qdrant indexes, audit exports, and external review imports.

## Structured Statistics

Create deterministic indexes from normalized battle records. Each battle should be expanded into both player-side records so win rates are not biased toward the observed leaderboard player side.

Core indexes:

- Card index: appearances, wins, losses, draws, usage rate, clean win rate, net win rate, rating, common teammates, common opponents.
- Deck index: sorted 8-card signature, games, wins, losses, draws, usage rate, win rate, net win rate, common opponents.
- Matchup index: deck signature A vs deck signature B, games, wins A, wins B, draws, crown averages, latest observed battle time.
- Archetype index: rule-based deck family classification with games, usage, win rate, representative decks, and confidence notes.

Default metric definitions:

- `clean_win_rate = wins / (wins + losses)`
- `net_win_rate = clean_win_rate - 50%`
- `usage_rate = appearances_or_games / total_side_records_or_battles`
- `rating = weighted score from Wilson win-rate lower bound, usage percentile, and sample confidence`

Every user-visible statistical answer must show:

- Snapshot ID.
- Collection time.
- Total sample battles.
- Matched sample count for the specific claim.
- Data-source boundary, especially when evidence is low.

## User Interfaces And APIs

### Structured UI

Add dedicated pages or tabs:

- Home / data status.
- Single-card stats.
- Card comparison.
- Exact 8-card deck profile.
- Exact 8-card deck-vs-deck matchup.
- Meta / archetype environment.

All card selection should use a strict visual picker with Chinese display names. Do not rely on a user-maintained alias table for structured card inputs.

### Free-Form QA UI

Keep the old free-form web page and old natural-language flow for analysis questions. It should continue to support:

- Natural-language card and deck questions.
- Multi-intent decomposition.
- RAG-backed meta analysis.
- RAG-backed deck environment positioning.
- RAG-backed card/deck pairing suggestions.
- Evidence-grounded model synthesis.

If the user asks for clan-war schedule or clan-war prep, return a clear removed-feature message and suggest supported data-analysis alternatives.

### API Surfaces

Add or adapt typed endpoints:

- `GET /api/cards/catalog`
- `GET /api/cards/{card_id}/stats`
- `POST /api/cards/compare`
- `POST /api/decks/profile`
- `POST /api/decks/matchup`
- `GET /api/meta/archetypes`
- `POST /api/qa/evidence`
- `GET /snapshot/status` with weekly retention, audit export, and RAG alignment fields.

Use consistent structured errors:

- `NO_MATCHUP_EVIDENCE` when exact deck-vs-deck data has zero matches.
- `LOW_SAMPLE_WARNING` metadata when sample size is below the chosen threshold.
- `UNSUPPORTED_CLAN_WAR_FEATURE` for removed clan-war schedule/prep requests.
- `UNSUPPORTED_SPECIAL_CARD_FIELDS` when tower/evolution/elite fields are requested but probe support is unavailable.

## RAG Design

Keep the current high-information-density approach: raw battles are the statistical pool, not one vector document per battle.

RAG document types should continue or expand from:

- Snapshot overview.
- Card documents.
- Deck documents.
- High-sample matchup documents.
- Card profiles.
- Deck profiles.
- Archetype documents.
- Card-pair synergy documents.
- Counter observation documents.

RAG quality and answer grounding should remain local whenever possible:

- Document generation is local and rule-based.
- Document validation is local and deterministic.
- RAG document fingerprinting is local.
- Local Qdrant index uses local Ollama embeddings by default.
- Cloud model calls are used only for parser fallback and final free-form answer synthesis.

## Implementation Tasks

### Phase 1: Branch, Docs, And Removed-Feature Boundary

Acceptance:

- New branch exists.
- This plan and ADR are committed or ready to commit.
- Clan-war schedule/prep is documented as removed from user-facing scope.

Verification:

- `git status --short --branch`
- Review docs under `docs/plans/` and `docs/decisions/`.

### Phase 2: Disable Clan-War Routing Without Deleting Infrastructure

Acceptance:

- Default skill registry no longer routes schedule query or schedule summary skills.
- Parser normalization and free-form QA return `UNSUPPORTED_CLAN_WAR_FEATURE` for schedule/prep requests.
- Existing schedule files and classes remain in repo unless deletion is later approved.

Verification:

- Unit tests for removed-feature rejection.
- Existing non-clan-war card/deck/RAG tests still pass.

### Phase 3: Weekly Snapshot And Retention

Acceptance:

- Snapshot target is configurable for 200,000 weekly battles.
- Snapshot status reports weekly collection progress and refresh cadence.
- Collector memory remains bounded and a stopped run resumes from its per-player SQLite checkpoint.
- Stream publication verifies the actual raw-record count before atomically moving the canonical pointer.
- Hourly monitoring performs no cloud model or embedding calls.
- Retention cleanup keeps at most current plus previous complete snapshot and at most 14 days.
- Cleanup never removes the active snapshot or the only rollback snapshot.

Verification:

- Snapshot lifecycle tests for success, failed refresh, rollback, and cleanup.
- Local dry-run retention test with fake snapshot directories.

### Phase 4: Raw Audit Export And External Review Import

Status: Implemented. The local export command materializes the package for the active snapshot.

Acceptance:

- Collector writes raw JSONL partitions and manifest with hashes.
- Export package includes a short README for ChatGPT web review.
- Reviewed RAG docs can be imported only after local validation.
- Invalid external review output is rejected without changing active RAG docs.

Verification:

- Tests for export manifest hashes.
- Tests for valid and invalid reviewed-doc import.

### Phase 5: Structured Stats Indexes

Status: Implemented. Run `python scripts/build_structured_stats.py` after a snapshot is published and audited.

Acceptance:

- Normalized battles are expanded into side records.
- Card, deck, matchup, and archetype indexes are derived from the same snapshot.
- All user-visible stats include matched sample count and snapshot provenance.

Verification:

- Unit tests with synthetic battles for wins, losses, draws, duplicate battles, low samples, and exact deck signatures.

### Phase 6: Structured APIs

Status: Implemented. The endpoints read only `data/structured_stats/{snapshot_id}/stats.sqlite`; they do not call the parser, model gateway, RAG, embeddings, or Supercell API.

Acceptance:

- Typed endpoints exist for card stats, card compare, deck profile, deck matchup, and archetype meta.
- Invalid card IDs, duplicate card selections, incomplete decks, zero-evidence matchups, and low-sample warnings return predictable structured responses.

Verification:

- API contract tests.
- Snapshot provenance tests on every endpoint.

### Phase 7: Frontend Structured Pages

Status: Implemented. The Web UI now exposes seven switchable views and proxies structured requests to the read-only snapshot API.

Acceptance:

- Card picker uses canonical card IDs and Chinese display names.
- Structured pages validate exact 8-card decks before submit.
- Special tower/evolution/elite controls are hidden or disabled until probe support exists.
- Free-form QA page remains available and continues to show trace/RAG status.

Verification:

- Browser/manual smoke test for each tab.
- Frontend tests or HTML contract tests for submit validation.

### Phase 8: Preserve And Expand Advanced RAG

Status: Implemented. Free-form QA retains natural-language and multi-intent routing; the environment page keeps its deterministic table and adds an explicit, user-triggered RAG synthesis action. Page load and refresh do not call the model.

Acceptance:

- Free-form meta analysis still retrieves snapshot evidence and calls the model for grounded synthesis.
- Environment type analysis uses structured archetype evidence plus RAG synthesis, not a pure table-only response.
- RAG document validation and quality gates remain snapshot-scoped.
- Numeric values must preserve evidence precision. Unsupported numeric sentences are removed locally while validated text is retained with a boundary notice; this repair path never retries the model or spends a second synthesis call.
- A final grounding failure returns a verified refusal and deterministic references instead of exposing a generic generation failure.

Verification:

- RAG tests for card/deck/meta analysis.
- Multi-intent tests mixing structured and RAG-backed subqueries.
- Grounding validation tests for unsupported numeric claims.
- Streaming and completed-response tests proving unsupported numeric sentences are withheld without weakening strict external API mode.

### Phase 9: Evaluation And Docs

Status: Implemented. Removed clan-war cases are negative boundary cases; structured endpoint contracts cover card, comparison, exact deck profile, matchup, zero evidence, and archetype responses. The static corpus continues to cover card/deck/meta and multi-intent routing.

Acceptance:

- Old successful schedule evaluation cases are removed or converted into removed-feature cases.
- New evaluation cases cover card stats, card comparison, deck profile, matchup, meta/archetype analysis, and free-form RAG.
- README and 00_START_HERE explain weekly snapshots, 14-day retention, audit exports, and cloud API cost boundaries.

Verification:

- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_tests.ps1`
- Deterministic evaluation runner passes updated corpus.

## Risks And Mitigations

| Risk | Impact | Mitigation |
|---|---:|---|
| Official API does not expose tower/evolution/elite fields reliably | High | First version supports exact 8-card decks only; probe fields and gate future UI behind evidence. |
| 200,000-battle collection exceeds practical refresh window | Medium | Weekly cadence, resumable raw export, strict fallback to previous complete snapshot, configurable target. |
| Exact deck-vs-deck matchup remains sparse | High | Return zero-evidence clearly; show 1-game results with sample warning; add archetype-level fallback as a separate analysis mode, not as exact-match evidence. |
| RAG docs drift from structured snapshot | High | Keep local deterministic validation and fingerprint alignment before activation. |
| External ChatGPT web review introduces inconsistent docs | Medium | Import reviewed docs only through local validation against snapshot hashes and structured metrics. |
| Raw audit exports become large | Medium | Partition JSONL, ignore large raw exports in git, retain at most two snapshot packages. |

## Open Decisions

- Exact Chinese card catalog source and update flow for new cards.
- Low-sample thresholds for warnings by feature, for example 1, 5, 20, or 50 games.
- Whether archetype fallback should be offered directly on zero exact deck-vs-deck evidence, or only as a separate button.
- Whether reviewed external RAG documents should replace generated docs or be stored as an additional source type.
