from __future__ import annotations

from collections import defaultdict
import threading
import time
from typing import Any, Callable

import requests


SUPERCELL_API_BASE_URL = "https://api.clashroyale.com/v1"
SUPERCELL_SOURCE_URL = "https://developer.clashroyale.com/"


class OfficialAPIRequester:
    """Low-level Supercell HTTP requester with bearer auth, pacing, and retries."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = SUPERCELL_API_BASE_URL,
        timeout_seconds: float = 5.0,
        session: Any | None = None,
        session_factory: Callable[[], Any] = requests.Session,
        max_retries: int = 0,
        requests_per_second: float = 0.0,
        sleeper: Callable[[float], Any] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not token:
            raise ValueError("SUPERCELL_API_TOKEN is required")
        self.token = token
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.session = session if session is not None else session_factory()
        self.max_retries = max(0, max_retries)
        self.requests_per_second = max(0.0, requests_per_second)
        self.sleeper = sleeper
        self.clock = clock
        self.metrics = defaultdict(float)
        self._pacer_lock = threading.Lock()
        self._next_request_at = 0.0
        self._cooldown_until = 0.0

    def _wait_for_request_slot(self) -> None:
        if self.requests_per_second <= 0:
            return
        with self._pacer_lock:
            now = self.clock()
            start_at = max(now, self._next_request_at, self._cooldown_until)
            self._next_request_at = start_at + 1.0 / self.requests_per_second
        wait_seconds = max(0.0, start_at - now)
        if wait_seconds:
            self.metrics["throttle_wait_seconds"] += wait_seconds
            self.sleeper(wait_seconds)

    def _apply_cooldown(self, seconds: float) -> None:
        with self._pacer_lock:
            self._cooldown_until = max(self._cooldown_until, self.clock() + max(0.0, seconds))

    @staticmethod
    def _retry_after_seconds(response: Any, attempt: int) -> float:
        headers = getattr(response, "headers", {}) or {}
        try:
            return max(0.0, float(headers.get("Retry-After", "")))
        except (TypeError, ValueError):
            return float(2**attempt)

    def get_json(self, path: str, *, params: dict | None = None):
        for attempt in range(self.max_retries + 1):
            try:
                self._wait_for_request_slot()
                self.metrics["request_count"] += 1
                response = self.session.get(
                    f"{self.base_url}{path}",
                    headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                    params=params,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, (dict, list)):
                    raise ValueError("official API returned an unsupported JSON payload")
                self.metrics["successful_requests"] += 1
                return payload
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                if getattr(response, "status_code", None) == 429:
                    self.metrics["rate_limited"] += 1
                    delay = self._retry_after_seconds(response, attempt)
                    self._apply_cooldown(delay)
                else:
                    delay = float(2**attempt)
                if attempt >= self.max_retries:
                    self.metrics["failed_requests"] += 1
                    raise
            except requests.RequestException:
                delay = float(2**attempt)
                if attempt >= self.max_retries:
                    self.metrics["failed_requests"] += 1
                    raise
            self.metrics["retried_requests"] += 1
            self.metrics["retry_wait_seconds"] += delay
            self.sleeper(delay)

    def _get_json(self, path: str, *, params: dict | None = None):
        return self.get_json(path, params=params)


__all__ = ["OfficialAPIRequester", "SUPERCELL_API_BASE_URL", "SUPERCELL_SOURCE_URL"]
