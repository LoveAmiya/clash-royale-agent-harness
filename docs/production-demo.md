# Production Demo Runbook

This runbook covers the `release/production-demo` branch. It integrates the
snapshot-scoped RAG fixes from `feature/quality-and-operations`; it does not
modify `main`.

## Runtime Protection

Local single-process execution keeps the zero-dependency memory quota. The
production Compose topology sets `PROCESS_QUOTA_BACKEND=redis`, so both API
instances share one atomic per-client rate window and one global concurrency
limit. Concurrency entries are leases with a default 300-second TTL, preventing
an interrupted SSE connection or crashed worker from reserving a slot forever.
Production uses `PROCESS_QUOTA_FAIL_MODE=closed`; Redis failure makes `/ready`
unavailable and `/process` returns 503 instead of silently bypassing protection.
Compose enables trusted proxy headers because API ports are internal-only and
Caddy owns `X-Forwarded-For`; direct local runs keep this disabled to prevent
callers from spoofing quota identities.

The ASGI request-body middleware validates both `Content-Length` and the bytes
actually received. Chunked requests without `Content-Length` are rejected with
413 as soon as the cumulative body exceeds `MAX_REQUEST_BODY_BYTES` (64 KiB by
default), before JSON validation or model execution.

Inspect the active limiter without exposing its URL or credentials:

```powershell
Invoke-RestMethod http://127.0.0.1:8091/health | Select-Object -ExpandProperty quota
Invoke-RestMethod http://127.0.0.1:8091/ready | Select-Object -ExpandProperty quota
```

## Production Compose

Validate the topology before launch:

```powershell
docker compose -f compose.production.yml config --quiet
docker compose -f compose.production.yml up --build -d
```

The stack contains two API instances, one collector, Redis, Caddy,
Prometheus, Alertmanager, a persistent alert webhook receiver, Grafana, Loki,
and Promtail. Redis, Prometheus, Alertmanager notifications, Grafana, and Loki
use named volumes. The webhook receiver rotates its JSONL store at 10 MiB and
redacts credential-shaped fields.

Do not start the containerized collector unless the container's stable public
egress IP is allowlisted by the Supercell key. Native Windows collection remains
the supported fallback for this workstation.

`https://localhost` uses Caddy's internal CA. Public HTTPS still requires a real
domain, DNS, and a publicly trusted ACME certificate.

## Load And Soak Tests

The isolated load topology starts two real API containers behind Caddy and one
shared Redis instance. It disables external model and Supercell calls, so the
test is repeatable and does not consume provider quota.

```powershell
powershell -ExecutionPolicy Bypass -File .\run_load_test.ps1 -Profile smoke
powershell -ExecutionPolicy Bypass -File .\run_load_test.ps1 -Profile load
powershell -ExecutionPolicy Bypass -File .\run_load_test.ps1 -Profile soak -SoakDuration 30m
```

Each run writes a timestamped JSON report under `load/reports/`, including when
a threshold fails. The checked thresholds cover SSE contract checks, failure
rate, response p95/p99, and first-byte p95.

Measured locally on 2026-07-27:

| Profile | Requests | Check rate | Failure rate | Process p95 | TTFB p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| smoke | 4 total HTTP / 3 process | 100% | 0% | 506.4 ms | 8.4 ms |
| load, 4 VU / 90 s | 510 total HTTP / 509 process | 100% | 0% | 397.6 ms | 2.8 ms |
| soak, 2 VU / 5 min | 1,009 total HTTP / 1,008 process | 100% | 0% | 403.4 ms | 2.6 ms |

The five-minute soak also measured process p99 at 408.9 ms and maximum at
462.8 ms. The checked 30-minute profile remains the pre-release endurance gate;
this implementation turn executed the bounded five-minute acceptance run.

These are workstation measurements, not a public-cloud capacity claim. Keep
the generated report when quoting numbers.

## Alert Delivery Drill

Production rules are routed from Prometheus to Alertmanager. Alertmanager
groups and deduplicates alerts, then sends firing and resolved notifications to
the persistent internal webhook receiver.

Run the complete delivery drill:

```powershell
powershell -ExecutionPolicy Bypass -File .\test_alert_pipeline.ps1
```

The drill creates an isolated Compose project, starts a one-second synthetic
Prometheus rule, waits up to 90 seconds for the persisted webhook count to
increase, writes `evaluation/reports/alert-drill-*.json`, and removes only its
temporary containers and volumes. The production alert timing remains
unchanged. The 2026-07-27 local drill delivered one notification successfully.

## CI Gates

CI now starts a real Redis service for the cross-instance quota integration
test. The container job validates all Compose files, runs the two-instance k6
smoke test, runs the Prometheus-to-Alertmanager drill, and uploads both success
and failure reports as artifacts.
