"""Small production safeguards shared by the FastAPI runtime.

The module intentionally uses only the standard library. The application is
deployed as one process today, so an in-memory quota is an explicit local
protection rather than a substitute for an edge proxy or distributed limiter.
"""

from __future__ import annotations

import asyncio
import hmac
import re
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass


_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SENSITIVE_FIELD_PATTERN = re.compile(r"(?:api[_-]?key|token|authorization|secret|password)", re.IGNORECASE)


def normalize_request_id(value: object) -> str:
    """Accept a bounded correlation ID or create a server-owned replacement."""
    candidate = str(value or "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return f"req-{uuid.uuid4().hex}"


def authorize_admin(expected_key: str | None, provided_key: str | None) -> bool:
    """Compare a configured administrator key without leaking partial matches."""
    if not expected_key or not provided_key:
        return False
    return hmac.compare_digest(expected_key, provided_key)


def redact_for_client(value: object) -> object:
    """Remove credential-shaped metadata before exposing traces or SSE events."""
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _SENSITIVE_FIELD_PATTERN.search(str(key)) else redact_for_client(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_for_client(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_client(item) for item in value]
    return value


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: str | None = None
    retry_after_seconds: int = 0


class ProcessQuota:
    """Per-process rate and concurrency guard for long-lived SSE requests."""

    def __init__(
        self,
        *,
        max_concurrent: int,
        requests_per_minute: int,
        window_seconds: float = 60.0,
        max_tracked_clients: int = 4096,
    ) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.requests_per_minute = max(0, int(requests_per_minute))
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_tracked_clients = max(16, int(max_tracked_clients))
        self._lock = asyncio.Lock()
        self._requests_by_client: dict[str, deque[float]] = defaultdict(deque)
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    async def try_acquire(self, client_id: str) -> QuotaDecision:
        now = time.monotonic()
        safe_client_id = client_id[:128] or "unknown"
        async with self._lock:
            timestamps = self._requests_by_client[safe_client_id]
            boundary = now - self.window_seconds
            while timestamps and timestamps[0] <= boundary:
                timestamps.popleft()

            if self.requests_per_minute and len(timestamps) >= self.requests_per_minute:
                retry_after = max(1, int(timestamps[0] + self.window_seconds - now) + 1)
                return QuotaDecision(False, "rate_limit", retry_after)
            if self._in_flight >= self.max_concurrent:
                return QuotaDecision(False, "concurrency", 1)

            timestamps.append(now)
            self._in_flight += 1
            self._prune_clients_locked(boundary)
            return QuotaDecision(True)

    def release(self) -> None:
        # The quota is used only from the FastAPI event loop. Keeping release
        # synchronous makes it safe to call in a streaming generator's finally.
        self._in_flight = max(0, self._in_flight - 1)

    def _prune_clients_locked(self, boundary: float) -> None:
        for client_id in list(self._requests_by_client):
            timestamps = self._requests_by_client[client_id]
            while timestamps and timestamps[0] <= boundary:
                timestamps.popleft()
            if not timestamps:
                self._requests_by_client.pop(client_id, None)
        if len(self._requests_by_client) > self.max_tracked_clients:
            # Per-client identities are untrusted and must not create unbounded
            # memory use. Clearing only expired data is preferred; if all are
            # active, reset accounting rather than retaining attacker input.
            self._requests_by_client.clear()


class RuntimeMetrics:
    """Low-cardinality Prometheus text metrics for this single-process runtime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, ...]] = Counter()
        self._sums: Counter[tuple[str, ...]] = Counter()
        self._last_snapshot_metrics: dict[str, float] = {}
        self._recent_process_durations: deque[float] = deque(maxlen=256)

    def record_http(self, *, route: str, status_code: int, duration_seconds: float) -> None:
        status_class = f"{max(0, int(status_code)) // 100}xx"
        key = (route, status_class)
        with self._lock:
            self._counters[("http_requests", *key)] += 1
            self._counters[("http_duration", *key)] += 1
            self._sums[("http_duration", *key)] += max(0.0, float(duration_seconds))

    def record_process(
        self,
        *,
        outcome: str,
        total_seconds: float,
        first_execution_seconds: float | None = None,
        first_content_seconds: float | None = None,
    ) -> None:
        with self._lock:
            self._counters[("process_requests", outcome)] += 1
            self._counters[("process_duration", outcome)] += 1
            self._sums[("process_duration", outcome)] += max(0.0, float(total_seconds))
            self._record_optional_duration("first_execution", outcome, first_execution_seconds)
            self._record_optional_duration("first_content", outcome, first_content_seconds)
            if outcome in {"success", "failure", "cancelled"}:
                self._recent_process_durations.append(max(0.0, float(total_seconds)))

    def record_model_stream(self, mode: str | None) -> None:
        if mode not in {"streaming", "fallback_chunked", "unavailable"}:
            return
        with self._lock:
            self._counters[("model_stream", mode)] += 1

    def record_snapshot_collection(self, collection_metrics: dict | None) -> None:
        if not isinstance(collection_metrics, dict):
            return
        allowed = {"request_count", "rate_limited", "retried_requests", "cache_hits", "collection_duration_seconds"}
        with self._lock:
            self._last_snapshot_metrics = {
                key: float(collection_metrics[key])
                for key in allowed
                if isinstance(collection_metrics.get(key), (int, float))
            }

    def _record_optional_duration(self, metric: str, outcome: str, value: float | None) -> None:
        if value is None:
            return
        self._counters[(metric, outcome)] += 1
        self._sums[(metric, outcome)] += max(0.0, float(value))

    def public_summary(self) -> dict[str, int | float]:
        """Return bounded operator-facing figures suitable for the browser UI."""
        with self._lock:
            outcomes = {outcome: int(self._counters[("process_requests", outcome)]) for outcome in ("success", "failure", "cancelled", "rate_limited")}
            durations = sorted(self._recent_process_durations)
            p95_ms = 0.0
            if durations:
                index = max(0, min(len(durations) - 1, int((len(durations) - 1) * 0.95)))
                p95_ms = round(durations[index] * 1000, 1)
            return {
                "process_requests": sum(outcomes.values()),
                "successes": outcomes["success"],
                "failures": outcomes["failure"],
                "cancelled": outcomes["cancelled"],
                "rate_limited": outcomes["rate_limited"],
                "process_p95_ms": p95_ms,
                "sample_size": len(durations),
            }

    @staticmethod
    def _labels(**values: object) -> str:
        return ",".join(f'{key}="{str(value)}"' for key, value in values.items())

    def render_prometheus(self, *, snapshot_status: str, rag_status: str, snapshot_aligned: bool) -> str:
        lines = [
            "# HELP cr_agent_http_requests_total Completed HTTP requests.",
            "# TYPE cr_agent_http_requests_total counter",
            "# HELP cr_agent_http_request_duration_seconds HTTP request duration.",
            "# TYPE cr_agent_http_request_duration_seconds summary",
            "# HELP cr_agent_process_requests_total Completed process requests.",
            "# TYPE cr_agent_process_requests_total counter",
            "# HELP cr_agent_runtime_state Current snapshot and RAG state.",
            "# TYPE cr_agent_runtime_state gauge",
        ]
        with self._lock:
            for (kind, *labels), value in sorted(self._counters.items()):
                if kind == "http_requests":
                    lines.append(f"cr_agent_http_requests_total{{{self._labels(route=labels[0], status_class=labels[1])}}} {value}")
                elif kind == "http_duration":
                    metric_labels = self._labels(route=labels[0], status_class=labels[1])
                    lines.append(f"cr_agent_http_request_duration_seconds_count{{{metric_labels}}} {value}")
                    lines.append(f"cr_agent_http_request_duration_seconds_sum{{{metric_labels}}} {self._sums[(kind, *labels)]:.6f}")
                elif kind == "process_requests":
                    lines.append(f"cr_agent_process_requests_total{{{self._labels(outcome=labels[0])}}} {value}")
                elif kind in {"process_duration", "first_execution", "first_content"}:
                    metric_name = {
                        "process_duration": "cr_agent_process_duration_seconds",
                        "first_execution": "cr_agent_process_first_execution_seconds",
                        "first_content": "cr_agent_process_first_content_seconds",
                    }[kind]
                    metric_labels = self._labels(outcome=labels[0])
                    lines.append(f"{metric_name}_count{{{metric_labels}}} {value}")
                    lines.append(f"{metric_name}_sum{{{metric_labels}}} {self._sums[(kind, *labels)]:.6f}")
                elif kind == "model_stream":
                    lines.append(f"cr_agent_model_stream_total{{{self._labels(mode=labels[0])}}} {value}")
            for key, value in sorted(self._last_snapshot_metrics.items()):
                lines.append(f"cr_agent_snapshot_collection_{key} {value}")
        lines.append(
            "cr_agent_runtime_state"
            f"{{{self._labels(snapshot_status=snapshot_status, rag_status=rag_status, snapshot_aligned=str(bool(snapshot_aligned)).lower())}}} 1"
        )
        return "\n".join(lines) + "\n"
