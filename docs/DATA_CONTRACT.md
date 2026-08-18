# Data Contract

This document defines the public data vocabulary for the project. It is the
first stop before changing structured statistics, RAG documents, snapshot
publication, collection materialization, or browser/API fields.

## Boundary

Public source control may contain code, deterministic fixtures, safe aliases,
tests, docs, ADRs, and configuration templates. It must not contain private
runtime data.

Private runtime data includes:

- raw Supercell battle logs and player identifiers
- rolling corpus SQLite databases and generated snapshot groups
- JSONL runtime traces, full logs, temporary staging databases, and generated reports
- provider keys, admin keys, PushPlus tokens, Supercell tokens, and request headers

If private runtime evidence is needed for a public explanation, summarize only
the aggregate finding and keep raw records out of Git.

## Contract Ownership

The data contract is implemented at package boundaries without changing the
public vocabulary: `collection` owns Supercell ingestion, battle normalization,
deduplication, rolling facts and materialization; `snapshots` owns publication
state and audit helpers; `stats` owns structured query semantics; `api` owns
dataset/status response wiring; and `qa` owns evidence-bound answer behavior.
Root modules and scheduled scripts remain compatibility entry points and must
not bypass these boundaries or read private artifacts into public responses.

## Dataset Scope

dataset_scope is a hard filter selected by the caller or UI. The model must not
choose, widen, narrow, or silently replace the selected scope.

Common scopes are rolling windows such as 7d_all and 35d_all. A valid scope
should carry enough metadata for auditability:

- snapshot group identifier
- scope name and time window
- unique battle count
- structured fact readiness
- RAG document readiness and fingerprint alignment

When a scope is unavailable, stale, or not aligned with RAG fingerprints, the
API should return an explicit readiness or boundary signal rather than falling
back to a different scope.

## Snapshot Group

A snapshot group is the unit published to the read-only API. It should become
active only after all required structured facts, RAG documents, metadata,
fingerprints, and validation gates align.

Publication rules:

- failed validation keeps the previous active snapshot group serving traffic
- empty batches must not publish as successful snapshots
- structured facts and RAG documents are published together
- active API startup reads the latest active snapshot group and does not mutate
  the rolling corpus

## Fact Levels

The project separates several fact levels:

| Level | Meaning | Typical use |
|---|---|---|
| battle observation | One observed player-side or batch-side record before global deduplication | collection diagnostics and duplicate-rate reporting |
| unique battle fact | One globally deduplicated battle keyed by battle_id | structured card, deck, matchup and meta statistics |
| complete loadout row | A player-side record with tower, eight card IDs, and card form metadata | full-loadout and entity statistics |
| RAG document | Aggregated evidence built from validated structured facts | open environment and archetype analysis |

Observation count can be much larger than unique battle count. Duplicate rate
should be reported as (observations - unique facts) / observations for a batch
or for the aggregate corpus, depending on context.

## Identifier Modes

The project keeps base-eight deck facts and full-loadout facts separate.

| Mode | Identifier source | Rule |
|---|---|---|
| base8 | Supercell canonical English card names for the eight cards | default structured deck path and legacy-compatible statistics |
| full_loadout | official numeric tower/card IDs plus evolution and elite metadata | higher-precision loadout and entity path |

Chinese names are display aliases and natural-language parser inputs. They are
not storage keys and must not be written into deck identifiers.

full_loadout identifiers must not fall back to base8 statistics when exact
loadout evidence is missing. Use explicit errors or boundary notices such as
INVALID_FULL_LOADOUT, NO_FULL_LOADOUT_EVIDENCE, or ENTITY_STATS_NOT_READY.

The detailed full-loadout contract remains in docs/FULL_LOADOUT_DATA_CONTRACT.md.

## Collection Lanes

The current collection design has two lanes:

| Lane | Mode | Contract |
|---|---|---|
| daily ranked | daily_ranked | current Path of Legend ranked seeds, up to top 1,000, no opponent expansion |
| expanded | weekly_expanded | exactly one legal Path of Legend opponent hop from ranked seeds |

Both lanes share global battle_id deduplication and publication gates. They
should not bypass writer locks, staging limits, disk checks, token/IP preflight,
or validation. A skipped trigger caused by an active writer is a protected no-op,
not a data failure.

## RAG Evidence

RAG documents are aggregated evidence derived from validated structured facts.
They are not raw battle records.

RAG answers must preserve:

- selected dataset_scope
- evidence document identifiers or summarized citations
- retrieval and rerank boundaries
- numeric grounding boundaries
- distinction between model synthesis, deterministic structured answers, and
  timeout/degraded evidence summaries

RAG document counts should distinguish current-scope evidence from full-library
evidence so the dashboard does not present a subset as the full corpus.

## API Reporting

Public or browser-facing status should prefer aggregate fields:

- collection mode and batch_id
- unique battle facts
- battle observations
- complete loadout rows
- duplicate rate
- snapshot group and readiness status
- validation pass/fail and failure class
- disk and staging usage by size only

Do not expose tokens, raw prompts, request headers, player tags, full raw logs,
or raw battle payloads in UI, SSE, logs meant for sharing, or public docs.

## Related Documents

- docs/FULL_LOADOUT_DATA_CONTRACT.md for exact full-loadout field rules.
- docs/ARCHITECTURE.md for runtime roles and publication invariants.
- docs/RAG_AND_QA.md for structured query, RAG and SSE behavior.
- docs/OPERATIONS.md for runtime and private-data operations.
- docs/decisions/README.md for ADRs that explain historical decisions.
