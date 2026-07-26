"""Thread-safe model provider circuit breaker and capability telemetry."""

from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class ModelCircuitOpenError(RuntimeError):
    pass


class ModelStreamingUnavailableError(RuntimeError):
    pass


class ModelProviderGuard:
    def __init__(
        self,
        *,
        provider_id: str,
        failure_threshold: int,
        recovery_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider_id = provider_id
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_seconds = max(1.0, float(recovery_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_in_flight = False
        self._last_error_type: str | None = None
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._capabilities = {"text_generation": "unknown", "streaming": "unknown"}
        self._capability_reason: str | None = None
        self._calls: Counter[tuple[str, str]] = Counter()
        self._stream_modes: Counter[str] = Counter()

    def before_call(self, operation: str) -> None:
        del operation
        now = self._clock()
        with self._lock:
            if self._state == "open":
                if now - self._opened_at < self.recovery_seconds:
                    raise ModelCircuitOpenError("model provider circuit is open")
                self._state = "half_open"
                self._half_open_in_flight = False
            if self._state == "half_open":
                if self._half_open_in_flight:
                    raise ModelCircuitOpenError("model provider half-open probe is already running")
                self._half_open_in_flight = True

    def record_success(self, operation: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._calls[(operation, "success")] += 1
            self._state = "closed"
            self._failures = 0
            self._half_open_in_flight = False
            self._last_error_type = None
            self._last_success_at = now
            self._capabilities["text_generation"] = "supported"

    def record_failure(self, operation: str, error: BaseException) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._calls[(operation, "failure")] += 1
            self._failures += 1
            self._last_error_type = type(error).__name__
            self._last_failure_at = now
            was_half_open = self._state == "half_open"
            self._half_open_in_flight = False
            if was_half_open or self._failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = self._clock()

    def record_cancelled(self, operation: str) -> None:
        with self._lock:
            self._calls[(operation, "cancelled")] += 1
            self._half_open_in_flight = False

    def record_stream_capability(self, *, supported: bool, reason: str | None = None) -> None:
        with self._lock:
            self._capabilities["streaming"] = "supported" if supported else "unsupported"
            self._capability_reason = reason

    def record_stream_mode(self, mode: str) -> None:
        if mode not in {"streaming", "fallback_chunked", "unavailable"}:
            return
        with self._lock:
            self._stream_modes[mode] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            remaining = 0.0
            if self._state == "open":
                remaining = max(0.0, self.recovery_seconds - (self._clock() - self._opened_at))
            return {
                "provider_id": self.provider_id,
                "circuit_state": self._state,
                "consecutive_failures": self._failures,
                "recovery_remaining_seconds": round(remaining, 3),
                "last_error_type": self._last_error_type,
                "last_success_at": self._last_success_at,
                "last_failure_at": self._last_failure_at,
                "capabilities": dict(self._capabilities),
                "stream_modes": {mode: int(self._stream_modes[mode]) for mode in ("streaming", "fallback_chunked", "unavailable")},
            }

    def render_prometheus(self) -> str:
        with self._lock:
            state = self._state
            calls = dict(self._calls)
            stream_modes = dict(self._stream_modes)
            streaming_capability = self._capabilities["streaming"]
        lines = [
            "# HELP cr_agent_model_provider_circuit Model provider circuit state.",
            "# TYPE cr_agent_model_provider_circuit gauge",
            f'cr_agent_model_provider_circuit{{provider="{self.provider_id}",state="{state}"}} 1',
            "# HELP cr_agent_model_provider_calls_total Model provider calls by operation and outcome.",
            "# TYPE cr_agent_model_provider_calls_total counter",
        ]
        for (operation, outcome), value in sorted(calls.items()):
            lines.append(
                f'cr_agent_model_provider_calls_total{{provider="{self.provider_id}",operation="{operation}",outcome="{outcome}"}} {value}'
            )
        for mode, value in sorted(stream_modes.items()):
            lines.append(f'cr_agent_model_stream_quality_total{{provider="{self.provider_id}",mode="{mode}"}} {value}')
        capability_value = {"unknown": 0, "unsupported": 0, "supported": 1}.get(streaming_capability, 0)
        lines.append(
            f'cr_agent_model_stream_capability{{provider="{self.provider_id}",status="{streaming_capability}"}} {capability_value}'
        )
        return "\n".join(lines) + "\n"
