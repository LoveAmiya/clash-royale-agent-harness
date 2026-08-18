# Architecture Decision Records

ADRs capture why the project made significant architectural choices. Do not delete old ADRs; supersede them with a new record when a decision changes.

## Index

| ADR | Topic | Area |
|---|---|---|
| [ADR-001](ADR-001-daily-official-snapshot.md) | Daily official snapshot | Data ingestion |
| [ADR-002](ADR-002-aggregated-rag-evidence-and-preheat.md) | Aggregated RAG evidence and preheat | RAG |
| [ADR-003](ADR-003-layered-evaluation-and-failure-reports.md) | Layered evaluation and failure reports | Testing |
| [ADR-004](ADR-004-production-runtime-guardrails.md) | Production runtime guardrails | Operations |
| [ADR-005](ADR-005-quality-feedback-and-split-runtime.md) | Feedback and split runtime | QA/Ops |
| [ADR-006](ADR-006-distributed-admission-and-alert-verification.md) | Distributed admission and alert verification | Operations |
| [ADR-007](ADR-007-weekly-meta-qa-snapshot-and-rag-boundary.md) | Weekly meta QA snapshot and RAG boundary | Data/RAG |
| [ADR-008](ADR-008-feature-weighted-deck-archetypes.md) | Feature-weighted deck archetypes | Analytics |
| [ADR-009](ADR-009-rolling-pol-corpus-and-scoped-publication.md) | Rolling POL corpus and scoped publication | Collection |
| [ADR-010](ADR-010-base8-and-full-loadout-facts.md) | Base8 and full-loadout facts | Data contract |
| [ADR-011](ADR-011-thirty-scopes-and-loadout-entities.md) | Thirty scopes and loadout entities | Data contract |
| [ADR-012](ADR-012-daily-expanded-path-of-legend-collection.md) | Daily expanded Path of Legend collection | Collection |
| [ADR-013](ADR-013-parallel-ranked-and-one-hop-collection.md) | Parallel ranked and one-hop collection | Collection/Ops |
| [ADR-014](ADR-014-bounded-multistage-rag-retrieval.md) | Bounded multistage RAG retrieval | RAG |
| [ADR-015](ADR-015-repository-health-and-compatible-package-migration.md) | Repository health and compatible package migration | Repository governance |

## Reading Guide

- Start with ADR-009 through ADR-013 for the rolling corpus and collection design.
- Read ADR-010 and ADR-011 before changing card, tower, evolution, elite or full-loadout data contracts.
- Read ADR-014 before changing retrieval fanout, fusion, rerank, compression or evidence counts.
- Read ADR-003 before changing test layers, generated reports or public CI gates.
- Read ADR-004 through ADR-006 before changing production deployment, quotas, alerts or operational surfaces.
- Read ADR-015 before moving modules, changing compatibility entry points, cleaning dead code, or handling private/generated repository artifacts.

## Lifecycle

- `Accepted`: current decision.
- `Superseded`: replaced by a later ADR; keep it for historical context.
- `Deprecated`: retained for context but no longer recommended.

When a major direction changes, add a new ADR and reference the prior decision it supersedes.
