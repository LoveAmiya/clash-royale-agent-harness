# ADR-006: Distributed Admission and Verifiable Alert Delivery

## Status

Accepted, 2026-07-27. Supersedes the multi-instance limiter decision in ADR-004.

## Context

The split runtime in ADR-005 runs two API processes. The in-memory quota from
ADR-004 therefore allowed each process its own full rate and concurrency budget.
`Content-Length` also did not protect requests that streamed a chunked body.
Prometheus rules existed, but no notification receiver or repeatable delivery
test proved that an alert left Prometheus. Container startup checks did not
measure the long-lived SSE request path under sustained concurrency.

## Decision

- Keep the in-memory quota for local one-process development. Production Compose
  uses one Redis quota shared by all API processes.
- Atomically evaluate the per-client sliding rate window and global concurrency
  set in one Redis script. Represent concurrency reservations as leases with a
  bounded TTL and remove them when the SSE request finishes.
- Fail closed in production if Redis is unavailable. Expose only sanitized quota
  state through `/health` and `/ready`; never expose the Redis URL.
- Count raw ASGI request bytes before framework parsing, buffering at most the
  configured request limit so chunked requests cannot bypass the boundary.
- Exercise two real API instances behind Caddy with k6 smoke, load, and soak
  profiles. Keep timestamped JSON summaries on both pass and threshold failure.
- Route Prometheus rules through Alertmanager to a persistent internal webhook.
  Verify the complete path with an isolated synthetic-rule drill and preserve a
  JSON result. Use production grouping/repeat timing outside the drill.

## Alternatives Considered

### Caddy-only rate limiting

Rejected for this branch because the existing Caddy image does not provide the
required distributed, per-client SSE lease semantics without a plugin build.
Edge limits can still complement the application quota.

### Continue per-process limits

Rejected because scaling from one to two API processes silently doubled both
published limits and made `/ready` unable to report shared admission health.

### Trust `Content-Length`

Rejected because HTTP clients may omit it and stream `http.request` chunks.

### Alert rules without a receiver drill

Rejected because configuration parsing and container health do not demonstrate
that Prometheus, Alertmanager grouping, and the notification destination agree.

## Consequences

- Multi-instance admission now depends on Redis. Fail-closed mode favors resource
  protection over availability; local development can explicitly use memory.
- Request bodies are read once into bounded memory before parsing. The default
  64 KiB limit makes this cost predictable and is appropriate for question JSON.
- Load tests are deterministic and quota-free with respect to external providers,
  but they benchmark the offline structured path rather than model-provider
  capacity or Supercell collection throughput.
- The internal webhook proves delivery and retains audit records. A real paging
  destination still requires organization-specific credentials and routing.
- Public HTTPS and stable Supercell container egress remain deployment concerns,
  not claims made by this ADR.

