# ADR-004: Production Runtime Guardrails

## Status

Accepted; multi-instance admission control superseded by ADR-006.

## Date

2026-07-26

## Context

The runtime exposes long-lived SSE requests that can trigger model inference,
retrieval, and official API snapshot work. A process-level health response alone
does not state whether strict external data is usable, and unbounded requests can
consume all local model capacity. The project also needed reproducible installs
and CI that never consumes credentials on ordinary pull requests.

## Decision

- Keep `/health` as a liveness endpoint and add `/ready` for model, snapshot,
  and RAG readiness. Strict mode returns `503` until a complete official snapshot
  and a model credential are available; RAG preheating is `degraded` rather than
  falsely `ready`.
- Assign or validate a bounded `X-Request-ID` for every HTTP request and include
  it in SSE execution/content events and trace metadata.
- Apply body/query caps, an in-memory per-client rate limit, and an in-memory
  concurrent-SSE limit. These are local safeguards; deployments with multiple
  workers must also enforce limits at the reverse proxy or gateway.
- Require `X-Admin-Key` for an explicitly enabled admin setting. An unset key
  keeps the endpoint closed.
- Export low-cardinality Prometheus text metrics at `/metrics`, surface a small
  operator summary in snapshot status, and never use request IDs or user input as
  metric labels.
- Pin direct and transitive packages in `requirements.lock.txt`; Docker and CI
  install that file. Regular CI uses no live credentials, while the manual smoke
  workflow requires a protected self-hosted runner with the approved Supercell IP.

## Consequences

- Operators can distinguish a live process from an answer-ready service and can
  correlate an SSE response to logs and trace metadata without logging questions.
- Public endpoints reject abusive inputs before model work starts.
- Local mode retains the in-memory limiter. ADR-006 replaces it with Redis-backed
  shared admission control for the two-instance production topology.
- Dependency refreshes require regenerating and reviewing the lock file.
