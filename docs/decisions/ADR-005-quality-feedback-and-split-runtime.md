# ADR-005: Snapshot Quality Gates, Feedback, and Split Runtime

## Status

Accepted, 2026-07-27.

## Context

The daily snapshot and preheated RAG pipeline were operational, but index
construction alone did not prove evidence quality. Model failures had no shared
resilience state, real user corrections were not retained, and starting multiple
workers would duplicate Supercell collection and contend for embedded Qdrant.

## Decision

- Activate a new retriever only after snapshot identity, evidence coverage,
  document uniqueness, and deterministic multi-probe per-source Recall@5 pass
  configured thresholds.
- Validate public RAG citations and bind unit-bearing numeric claims to their
  metric and known card entity before treating an answer as grounded.
- Put parser and synthesis model calls behind one provider circuit breaker;
  passively detect stream support from actual public deltas and report explicit
  fallbacks plus per-mode first-content and total latency.
- Accept feedback only for server-owned completed request IDs. Store corrections
  as review candidates, not automatic training data or deterministic assertions;
  preserve manual review decisions and stable assertions across repeated exports.
- Separate runtime roles into `collector`, `api`, and backward-compatible `all`.
  Only the collector contacts Supercell. API processes poll atomic snapshot files
  and build process-local in-memory indexes.
- Use two independently scraped API processes behind Caddy instead of opaque
  Uvicorn workers, preserving per-process metrics. Persist answer ownership and
  feedback in shared SQLite so load balancing does not lose request IDs.
- Provision a Grafana operations dashboard, Prometheus symptom rules, centralized
  Loki logs, and bounded persistent retention for metrics and logs.

## Consequences

RAG availability can remain on the previous active index or fail closed when a
new snapshot does not meet quality thresholds. Feedback requires a writable
shared data directory. Each API process performs its own embedding preheat. The
provided local TLS uses Caddy's internal CA; public deployments must use ACME or
an organization-managed certificate. Containerized Supercell collection still
requires a stable allowlisted egress IP.
