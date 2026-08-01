# ADR-007: Weekly Meta QA Snapshot And RAG Boundary

## Status

Accepted

## Date

2026-07-28

## Context

The project began as a Clash Royale agent workflow with clan-war schedule lookup, clan-war preparation advice, structured card/deck answers, and RAG-backed open analysis. The new product direction is a broader Clash Royale data QA system based on official Supercell API evidence.

The key constraints are:

- Official API battle logs can support sampled card, deck, and matchup statistics, but do not provide a global all-battle statistics warehouse.
- The analytical population must be Path of Legend only. Mixing `PvP`, clan-mate, event, or ordinary ladder records changes the population and invalidates environment conclusions.
- Exact deck-vs-deck evidence is inherently sparse, so every result must expose matched sample size.
- Free-form high-end analysis still needs RAG and model synthesis; table-only structured queries are insufficient for environment analysis, deck positioning, and multi-intent questions.
- The user wants to remove clan-war capabilities, not the engineering foundation.
- Cloud API use should be bounded. Slicing, validation, and embedding should be local by default; optional ChatGPT web review should use separated export files and not the project API key.

## Decision

Use a weekly official snapshot model for the meta QA system:

- Collect a complete weekly official Supercell battle-log snapshot with a default target of 200,000 usable battles.
- Seed from the global Path of Legend top 1,000, then expand only through opponent tags found in accepted `pathOfLegend` battles. A rejected battle cannot contribute data or queue entries.
- Persist `collection_scope`, a versioned scope contract, and each normalized record's official battle type. A new-scope collector never resumes a legacy mixed-mode work area.
- Keep at most 14 days of data and at most 2 complete snapshots: current active plus one previous rollback snapshot.
- Preserve free-form web QA, natural-language parsing, multi-intent decomposition, advanced RAG, traceability, feedback, monitoring, model resilience, and strict snapshot/RAG alignment.
- Keep remote structured intent parsing mandatory for the free-form QA page. The environment-analysis page may send only the validated `meta_analysis_query` interface hint because the selected page already establishes that intent; this hint skips routing, not RAG retrieval or model synthesis.
- Remove clan-war schedule lookup and clan-war preparation advice from user-facing routing.
- Ship first with exact 8-card deck support only. Tower/evolution/elite support requires a field probe and a later explicit enablement decision.
- Generate high-information-density RAG documents from structured snapshot aggregates, not one document per raw battle.
- Collect into a resumable SQLite work area and stream normalized JSONL during publication. Never retain both original API logs and all normalized battles in process memory.
- Keep complete exact matchup aggregates in the archived SQLite sidecar while capping the JSON/RAG-facing matchup summary at 20,000 highest-sample rows.
- Export raw and normalized audit packages separately so the user can review or slice them with ChatGPT web without consuming the project cloud API key.
- Validate any imported externally reviewed slices locally before they can affect active RAG.
- Route application model calls through `https://crs.ruinique.com` using the Responses API, `gpt-5.5`, and `medium` reasoning. This reduces interactive latency while preserving evidence-grounded synthesis. Only the credential comes from `OPENAI_API_KEY`; machine-level provider URLs cannot redirect the project to the official endpoint.

## Alternatives Considered

### Make Everything Structured And Drop RAG

Pros:

- Lower model cost.
- Easier deterministic testing.
- Less ambiguity in responses.

Cons:

- Cannot preserve existing high-end free-form analysis.
- Environment analysis and multi-intent questions become table dumps rather than useful explanations.
- Throws away existing RAG quality and grounding infrastructure.

Rejected because the product needs both deterministic stats and evidence-grounded natural-language analysis.

### Keep Daily 20,000-Battle Snapshots

Pros:

- Already implemented and fast enough, roughly 30 minutes in current observation.
- Lower storage and API pressure.

Cons:

- Exact deck-vs-deck questions are too sparse.
- Frequent daily overlap may overcount repeated battle-log windows.
- Smaller sample reduces confidence for archetype and matchup evidence.

Rejected for the new product direction. Weekly 200,000-battle snapshots better match the desired analytical depth.

### Store Every Raw Battle As A Vector Document

Pros:

- Maximum raw traceability in vector search.

Cons:

- Poor RAG density.
- Larger index with many repetitive low-signal documents.
- More embedding time without better analysis quality.

Rejected. Raw battles remain auditable data; RAG gets compact aggregate evidence.

### Use Cloud Embeddings And Cloud Vector Database

Pros:

- Easier scaling if data grows far beyond current needs.
- Less local dependency on Ollama.

Cons:

- Consumes cloud API or database budget.
- Adds external operational dependencies.
- Current document count is small enough for local Qdrant.

Rejected for default operation. Local Ollama and local Qdrant remain the default; cloud alternatives can be a later optional deployment mode.

### Delete Schedule Code And Data Immediately

Pros:

- Less dead code.
- Cleaner product surface.

Cons:

- Higher risk of deleting useful infrastructure or tests before the new boundary is stable.
- User explicitly wants bottom-layer engineering preserved unless deletion is justified and approved.

Rejected for first implementation. Schedule capabilities will be unregistered and removed from UI/evaluation, but code/data deletion requires a separate explicit decision.

## Consequences

- The system will be clearer: structured pages answer deterministic questions, free-form QA answers evidence-backed analysis questions.
- Snapshot collection becomes longer, expected around 5 to 8 hours for 200,000 battles based on the current 20,000-battle/30-minute observation.
- The previous 200,000-battle snapshot is retained for rollback but is considered legacy mixed-mode evidence until the first Path-of-Legend-only replacement passes validation.
- A collector crash no longer discards already committed players; an incomplete workspace can resume for up to 14 days and cannot replace the last complete snapshot.
- Publication temporarily uses additional F-drive disk space for the work database, JSONL, archive, and canonical files, but collector memory is bounded. A 200,000-record synthetic benchmark grew RSS by 4.2 MB for the collection implementation.
- Retention stays bounded: current plus previous snapshot only.
- Exact matchup answers must sometimes say there is no evidence, or show very low sample size.
- RAG remains valuable and must be maintained as a first-class path.
- Environment-page analysis makes one RAG synthesis request instead of asking the parser to reinterpret a fixed prompt and potentially create unrelated subqueries. Free-form QA cannot use the page hint and continues through remote structured parsing.
- External ChatGPT web review becomes a supported manual workflow through audit exports and local import validation.
- Future tower/evolution/elite support depends on observed official battle-log fields, not speculation.
