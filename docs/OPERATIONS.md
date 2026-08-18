# Operations Guide

## Public and Private Boundaries

The repository publishes application code, tests, documentation, safe configuration templates, and the reviewed Chinese alias catalog. Runtime facts are private operational data and must stay out of source control, issue reports, container images, and public examples.

Operational surfaces have different exposure rules:

| Surface | Boundary |
|---|---|
| /health | Safe for local process liveness checks |
| /ready | Readiness and data-alignment signal; keep inside trusted deployment paths |
| /metrics | Prometheus surface; expose only on localhost, a private network, or behind a trusted reverse proxy |
| /model/status | Provider and circuit status; keep private or protected |
| /feedback/stats | Review and feedback operational surface; keep private or protected |

Production exposure should use a reverse proxy or explicit ADMIN_API_KEY boundary for administrative and operational endpoints. Do not publish raw traces, player tags, battle logs, request headers, provider keys, or Supercell tokens.

This guide is the public operational map for running the project. It owns
configuration, exposure boundaries, private runtime state, deployment, host
requirements, and operational troubleshooting. It does not duplicate API/Web
startup commands or collector task commands.

Document ownership:

- `00_START_HERE.md`: local API and browser startup, readiness checks, and safe API/Web restart.
- `docs/SNAPSHOT_COLLECTION_HANDOFF.md`: scheduled collector installation, start/stop, recovery, status checks, PushPlus, batch acceptance, and publication recovery.
- `docs/TESTING.md`: public gates, generated reports, and live-smoke policy.

## Local Startup

Follow `00_START_HERE.md` for the authoritative local install, public gate,
API/Web start, readiness check, and safe restart commands. This document only
records the operational surfaces and their exposure boundaries.

Default local endpoints:

| Surface | URL | Notes |
|---|---|---|
| Browser UI | `http://127.0.0.1:8080` | Local browser interface |
| API health | `http://127.0.0.1:8091/health` | Process-level health |
| API process endpoint | `http://127.0.0.1:8091/process` | Main QA endpoint |
| Metrics | `http://127.0.0.1:8091/metrics` | Operational metrics; keep trusted/private |

## Startup and Preheat Baselines

`/health` includes a read-only `performance_baseline` object when the runtime
has completed startup or attempted RAG preheat. `api_startup.elapsed_seconds`
measures API lifecycle completion time. `rag_preheat` records the latest
attempt's `elapsed_seconds`, `outcome` (`ready`, `reused`, `failed`,
`discarded`, `busy`, or `not_ready`), and snapshot identifier. These are
aggregate diagnostics only; they do not expose prompts, documents, battle
facts, player tags, or provider credentials. Use the local evaluation reports
described in `evaluation/reports/README.md` for retained benchmark history.

## Configuration

Common configuration groups:

| Group | Keys |
|---|---|
| Runtime ports and CORS | RUNTIME_PORT, WEB_PORT, BACKEND_URL, ALLOWED_ORIGINS |
| Provider wire settings | OPENAI_BASE_URL, OPENAI_WIRE_API, OPENAI_MODEL, OPENAI_REVIEW_MODEL |
| Reasoning and timeout budgets | OPENAI_REASONING_EFFORT, PARSER_REASONING_EFFORT, SYNTHESIS_REASONING_EFFORT, PARSER_CALL_TIMEOUT_SECONDS, MODEL_CALL_TIMEOUT_SECONDS, MODEL_FIRST_TOKEN_TIMEOUT_SECONDS |
| Request safety | MAX_REQUEST_BODY_BYTES, MAX_QUERY_CHARS, PROCESS_MAX_CONCURRENT, PROCESS_RATE_LIMIT_PER_MINUTE |
| Embeddings | OLLAMA_EMBED_URL, EMBED_MODEL, OLLAMA_EMBED_TIMEOUT_SECONDS |
| Operations and collection | ADMIN_API_KEY, SUPERCELL_API_TOKEN, SUPERCELL_API_TOKENS, SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS |

Defaults are loopback-oriented. Set RUNTIME_HOST=0.0.0.0 only for a deliberate container or reverse-proxy deployment. A caller may provide a bounded X-Request-ID; the runtime returns it in HTTP headers, SSE execution/content events, and the final trace.

- `.env.example` documents public configuration names.
- Real `OPENAI_API_KEY`, `SUPERCELL_API_TOKEN`, `SUPERCELL_API_TOKENS`, notification tokens and admin secrets must live in the current process, Windows user environment, or deployment secret store.
- Do not commit real credentials, private paths, copied stdout/stderr logs, raw JSONL traces, SQLite databases or private exports.
- Restart the relevant process after changing Windows user-level environment variables.

## Private Runtime Data

These paths are private runtime state and are ignored by Git:

- `data/corpus/`
- `data/snapshot_groups/`
- `data/rolling_lanes/`
- `data/structured_stats/`
- `logs/`
- `tmp/`
- `evaluation/reports/`
- `evaluation/*report*.json`

The only tracked file under `data/` should be the reviewed safe alias catalog. If a generated report contains an important result, summarize it in Markdown instead of committing the raw report.

## Collection Operations

Collection is owned by Windows scheduled tasks and backend scripts, not by
Codex timers. The detailed task names, modes, token slots, one-hop boundary,
deduplication/publication invariants, installation, start/stop, recovery, and
PushPlus behavior are owned by `docs/SNAPSHOT_COLLECTION_HANDOFF.md`. Do not
restart or reconfigure a collector while performing API/Web maintenance.

## Host and Disk Boundaries

- The computer must be powered on and Windows must stay awake for scheduled collection.
- The display may turn off normally; sleep, hibernation, shutdown and power loss pause collection.
- The project expects collection temporary files and SQLite work to stay on the project drive, not the system drive.
- Do not start collection if the project drive has less than the configured safety threshold.

## Production Compose

`compose.production.yml` describes a split deployment with read-only API instances, collector role, Caddy, Redis quota, Prometheus, Alertmanager, Loki, Promtail and Grafana.

```powershell
docker compose -f compose.production.yml config --quiet
docker compose -f compose.production.yml up --build -d
```

Only run a containerized collector when the container egress IP is stable and allowlisted by Supercell. On local Windows, native collection plus containerized read-only services is usually safer.

## Single-Container Packaging Smoke

~~~powershell
docker build -t clash-royale-agent .
docker run --rm -p 8091:8091 --env-file .env clash-royale-agent
~~~

The container health check validates packaging and liveness. It does not prove Supercell IP authorization, live model access, current snapshot freshness, or scheduled collection readiness.

## Readiness and Troubleshooting

- Use `/health` for process liveness only.
- Use snapshot/RAG status and the browser operations dashboard to confirm data alignment.
- Use logs only in short, redacted tails. Never paste tokens, player tags, raw battle logs or full status JSON into issue reports.
- If facts are accepted but publication fails, retry publication before recollecting the same batch.

## Related Documents

- `00_START_HERE.md` for quick local startup.
- `docs/SNAPSHOT_COLLECTION_HANDOFF.md` for collection operations.
- `docs/TESTING.md` for verification gates.
- `docs/ARCHITECTURE.md` for runtime boundaries.
- `docs/DATA_CONTRACT.md` for data scopes, identifiers and reporting vocabulary.
- `docs/REPO_HEALTH_PLAN.md` for cleanup sequencing.
