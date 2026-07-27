"""Production safeguards shared by the FastAPI runtime.

Local runs use an in-memory quota. Multi-instance deployments select the Redis
backend so every API process shares the same rate and concurrency boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import re
import threading
import time
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass


_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SENSITIVE_FIELD_PATTERN = re.compile(r"(?:api[_-]?key|token|authorization|secret|password)", re.IGNORECASE)


class RequestBodyLimitMiddleware:
    """Enforce a byte limit from ASGI receive messages, including chunked bodies."""

    def __init__(self, app, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max(1, int(max_body_bytes))

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        request_id = normalize_request_id(headers.get(b"x-request-id", b"").decode("ascii", errors="ignore"))
        scope.setdefault("state", {})["request_id"] = request_id
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await self._send_error(send, 400, "invalid_content_length", request_id)
                return
            if declared_length < 0:
                await self._send_error(send, 400, "invalid_content_length", request_id)
                return
            if declared_length > self.max_body_bytes:
                await self._send_error(send, 413, "request_body_too_large", request_id)
                return

        received_bytes = 0
        buffered_messages = []
        while True:
            message = await receive()
            buffered_messages.append(message)
            if message.get("type") != "http.request":
                break
            received_bytes += len(message.get("body", b""))
            if received_bytes > self.max_body_bytes:
                await self._send_error(send, 413, "request_body_too_large", request_id)
                return
            if not message.get("more_body", False):
                break

        message_index = 0

        async def replay_receive():
            nonlocal message_index
            if message_index < len(buffered_messages):
                message = buffered_messages[message_index]
                message_index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _send_error(send, status_code: int, error: str, request_id: str) -> None:
        body = json.dumps({"error": error, "request_id": request_id}, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"x-request-id", request_id.encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


def normalize_request_id(value: object) -> str:
    """Accept a bounded correlation ID or create a server-owned replacement."""
    candidate = str(value or "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return f"req-{uuid.uuid4().hex}"


def resolve_client_id(remote_host: str | None, forwarded_for: str | None, *, trust_proxy_headers: bool) -> str:
    """Resolve a quota identity without trusting caller-controlled headers by default."""
    remote = str(remote_host or "unknown")[:128]
    if not trust_proxy_headers or not forwarded_for:
        return remote
    candidate = forwarded_for.split(",", 1)[0].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return remote


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
    lease_id: str | None = None


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
        self._leases: set[str] = set()

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

            lease_id = uuid.uuid4().hex
            timestamps.append(now)
            self._in_flight += 1
            self._leases.add(lease_id)
            self._prune_clients_locked(boundary)
            return QuotaDecision(True, lease_id=lease_id)

    async def release(self, lease_id: str | None = None) -> None:
        async with self._lock:
            if lease_id is not None and lease_id not in self._leases:
                return
            if lease_id is not None:
                self._leases.discard(lease_id)
            self._in_flight = max(0, self._in_flight - 1)

    async def close(self) -> None:
        return None

    async def probe(self) -> bool:
        return True

    def status(self) -> dict[str, object]:
        return {"backend": "memory", "available": True, "in_flight": self._in_flight}

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


_REDIS_ACQUIRE_SCRIPT = """
local now = redis.call('TIME')
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
local window_ms = tonumber(ARGV[4])
local lease_ms = tonumber(ARGV[5])
local max_concurrent = tonumber(ARGV[2])
local rate_limit = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now_ms - window_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now_ms)

local rate_count = redis.call('ZCARD', KEYS[1])
if rate_limit > 0 and rate_count >= rate_limit then
  local earliest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry_ms = window_ms
  if earliest[2] then
    retry_ms = math.max(1000, tonumber(earliest[2]) + window_ms - now_ms)
  end
  return {0, 1, retry_ms}
end

if redis.call('ZCARD', KEYS[2]) >= max_concurrent then
  local earliest_lease = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
  local retry_ms = 1000
  if earliest_lease[2] then
    retry_ms = math.max(1000, tonumber(earliest_lease[2]) - now_ms)
  end
  return {0, 2, retry_ms}
end

redis.call('ZADD', KEYS[1], now_ms, ARGV[1])
redis.call('PEXPIRE', KEYS[1], window_ms + 1000)
redis.call('ZADD', KEYS[2], now_ms + lease_ms, ARGV[1])
redis.call('PEXPIRE', KEYS[2], lease_ms + 1000)
return {1, 0, 0}
"""

_REDIS_RELEASE_SCRIPT = "return redis.call('ZREM', KEYS[1], ARGV[1])"


class RedisProcessQuota:
    """Atomic cross-process quota backed by Redis sorted sets and expiring leases."""

    def __init__(
        self,
        redis_client,
        *,
        max_concurrent: int,
        requests_per_minute: int,
        lease_seconds: float,
        key_prefix: str = "cr-agent:process-quota",
        window_seconds: float = 60.0,
        fail_mode: str = "closed",
    ) -> None:
        self._redis = redis_client
        self.max_concurrent = max(1, int(max_concurrent))
        self.requests_per_minute = max(0, int(requests_per_minute))
        self.lease_seconds = max(1.0, float(lease_seconds))
        self.window_seconds = max(1.0, float(window_seconds))
        self.key_prefix = key_prefix.strip().rstrip(":") or "cr-agent:process-quota"
        self.fail_mode = "open" if fail_mode == "open" else "closed"
        self._available = True
        self._last_error: str | None = None

    async def try_acquire(self, client_id: str) -> QuotaDecision:
        token = uuid.uuid4().hex
        client_digest = hashlib.sha256((client_id[:256] or "unknown").encode("utf-8")).hexdigest()[:32]
        rate_key = f"{self.key_prefix}:rate:{client_digest}"
        inflight_key = f"{self.key_prefix}:inflight"
        try:
            result = await self._redis.eval(
                _REDIS_ACQUIRE_SCRIPT,
                2,
                rate_key,
                inflight_key,
                token,
                self.max_concurrent,
                self.requests_per_minute,
                int(self.window_seconds * 1000),
                int(self.lease_seconds * 1000),
            )
            self._available = True
            self._last_error = None
        except Exception as exc:
            self._available = False
            self._last_error = type(exc).__name__
            if self.fail_mode == "open":
                return QuotaDecision(True, reason="quota_backend_bypassed")
            return QuotaDecision(False, "quota_backend_unavailable", 1)

        allowed, reason_code, retry_ms = (int(result[0]), int(result[1]), int(result[2]))
        if allowed:
            return QuotaDecision(True, lease_id=token)
        reason = "rate_limit" if reason_code == 1 else "concurrency"
        return QuotaDecision(False, reason, max(1, (retry_ms + 999) // 1000))

    async def release(self, lease_id: str | None = None) -> None:
        if not lease_id:
            return
        try:
            await self._redis.eval(
                _REDIS_RELEASE_SCRIPT,
                1,
                f"{self.key_prefix}:inflight",
                lease_id,
            )
            self._available = True
            self._last_error = None
        except Exception as exc:
            self._available = False
            self._last_error = type(exc).__name__

    async def close(self) -> None:
        close = getattr(self._redis, "aclose", None)
        if close is not None:
            await close()

    async def probe(self) -> bool:
        try:
            await self._redis.ping()
            self._available = True
            self._last_error = None
            return True
        except Exception as exc:
            self._available = False
            self._last_error = type(exc).__name__
            return False

    def status(self) -> dict[str, object]:
        return {
            "backend": "redis",
            "available": self._available,
            "fail_mode": self.fail_mode,
            "last_error_type": self._last_error,
        }


def create_process_quota(
    *,
    backend: str,
    max_concurrent: int,
    requests_per_minute: int,
    redis_url: str = "",
    lease_seconds: float = 300,
    key_prefix: str = "cr-agent:process-quota",
    fail_mode: str = "closed",
):
    """Create the configured quota without importing Redis for local memory mode."""
    if backend != "redis":
        return ProcessQuota(
            max_concurrent=max_concurrent,
            requests_per_minute=requests_per_minute,
        )
    if not redis_url:
        raise ValueError("REDIS_URL is required when PROCESS_QUOTA_BACKEND=redis")
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
    return RedisProcessQuota(
        client,
        max_concurrent=max_concurrent,
        requests_per_minute=requests_per_minute,
        lease_seconds=lease_seconds,
        key_prefix=key_prefix,
        fail_mode=fail_mode,
    )


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

    def record_model_stream(
        self,
        mode: str | None,
        *,
        first_content_seconds: float | None = None,
        total_seconds: float | None = None,
    ) -> None:
        if mode not in {"streaming", "fallback_chunked", "unavailable"}:
            return
        with self._lock:
            self._counters[("model_stream", mode)] += 1
            self._record_optional_duration("model_stream_first_content", mode, first_content_seconds)
            self._record_optional_duration("model_stream_duration", mode, total_seconds)

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
                elif kind in {"model_stream_first_content", "model_stream_duration"}:
                    metric_name = {
                        "model_stream_first_content": "cr_agent_model_stream_first_content_seconds",
                        "model_stream_duration": "cr_agent_model_stream_duration_seconds",
                    }[kind]
                    metric_labels = self._labels(mode=labels[0])
                    lines.append(f"{metric_name}_count{{{metric_labels}}} {value}")
                    lines.append(f"{metric_name}_sum{{{metric_labels}}} {self._sums[(kind, *labels)]:.6f}")
            for key, value in sorted(self._last_snapshot_metrics.items()):
                lines.append(f"cr_agent_snapshot_collection_{key} {value}")
        lines.append(
            "cr_agent_runtime_state"
            f"{{{self._labels(snapshot_status=snapshot_status, rag_status=rag_status, snapshot_aligned=str(bool(snapshot_aligned)).lower())}}} 1"
        )
        return "\n".join(lines) + "\n"
