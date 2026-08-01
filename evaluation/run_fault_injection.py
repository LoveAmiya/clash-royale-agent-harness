"""Deterministic, offline fault-injection evaluation.

This runner exercises policy state machines with synthetic faults. It never calls
the model provider, Supercell, or any other external service, and its report says
so explicitly. It is a regression gate, not evidence of live-service reliability.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from model_resilience import ModelCircuitOpenError, ModelProviderGuard
from rag_quality import validate_answer_grounding
from runtime_hardening import ProcessQuota
from evaluation.scorecard import attach_scorecard


ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_FILE = ROOT / "evaluation" / "fault_scenarios.jsonl"
REPORTS_DIR = ROOT / "evaluation" / "reports"

REQUIRED_CATEGORIES = {
    "model_circuit",
    "stream_unavailable_fallback",
    "supercell_429_retry_cooldown",
    "request_quota_rate_limit",
    "stale_snapshot_rag_alignment",
    "grounding_citation_number",
}

MEASURED_FIELDS = (
    "outcome",
    "successful_degradation_or_recovery",
    "repeated_external_requests",
    "recovery_or_handling_latency_ms",
)


def load_scenarios(path: Path = SCENARIOS_FILE) -> list[dict[str, Any]]:
    scenarios = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [item.get("id") for item in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("fault scenario ids must be unique")
    categories = {item.get("category") for item in scenarios}
    if path == SCENARIOS_FILE and categories != REQUIRED_CATEGORIES:
        raise ValueError(f"fault scenario categories differ: {categories ^ REQUIRED_CATEGORIES}")
    return scenarios


def _result(outcome: str, latency_ms: int, repeats: int = 0) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "successful_degradation_or_recovery": True,
        "repeated_external_requests": repeats,
        "recovery_or_handling_latency_ms": latency_ms,
    }


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _model_circuit(variant: str, data: dict[str, Any]) -> dict[str, Any]:
    request_latency = int(data.get("request_latency_ms", 0))
    fallback_latency = int(data.get("fallback_latency_ms", 0))
    if variant == "transient_recovery":
        failures = int(data["failures_before_success"])
        retries = min(failures, int(data["max_retries"]))
        attempts = retries + 1
        backoff = sum(data.get("backoff_ms", [])[:retries])
        recovered = failures <= retries
        guard = ModelProviderGuard(
            provider_id="fault-injection",
            failure_threshold=int(data["max_retries"]) + 1,
            recovery_seconds=30,
        )
        for attempt in range(attempts):
            guard.before_call("generate")
            if attempt < failures:
                guard.record_failure("generate", RuntimeError("injected transient failure"))
            else:
                guard.record_success("generate")
        recovered = recovered and guard.snapshot()["circuit_state"] == "closed"
        return _result("recovered" if recovered else "unavailable", attempts * request_latency + backoff, retries)
    if variant == "threshold_failure":
        guard = ModelProviderGuard(
            provider_id="fault-injection",
            failure_threshold=int(data["failure_threshold"]),
            recovery_seconds=30,
        )
        for _ in range(int(data["prior_failures"])):
            guard.before_call("generate")
            guard.record_failure("generate", RuntimeError("injected provider failure"))
        guard.before_call("generate")
        guard.record_failure("generate", RuntimeError("injected threshold failure"))
        circuit_opens = guard.snapshot()["circuit_state"] == "open"
        outcome = "fallback_chunked" if circuit_opens and data.get("fallback_available") else "unavailable"
        return _result(outcome, request_latency + (fallback_latency if data.get("fallback_available") else 0))
    if variant == "open_short_circuit":
        guard = ModelProviderGuard(
            provider_id="fault-injection",
            failure_threshold=1,
            recovery_seconds=30,
        )
        guard.before_call("generate")
        guard.record_failure("generate", RuntimeError("injected provider failure"))
        try:
            guard.before_call("generate")
        except ModelCircuitOpenError:
            pass
        else:
            raise AssertionError("open model circuit allowed an external request")
        outcome = "fallback_chunked" if data.get("fallback_available") else "unavailable"
        return _result(outcome, fallback_latency if data.get("fallback_available") else 0)
    if variant == "half_open_probe":
        clock = _Clock()
        guard = ModelProviderGuard(
            provider_id="fault-injection",
            failure_threshold=1,
            recovery_seconds=30,
            clock=clock,
        )
        guard.before_call("generate")
        guard.record_failure("generate", RuntimeError("injected provider failure"))
        clock.value = 31
        guard.before_call("probe")
        if data.get("probe_succeeds"):
            guard.record_success("probe")
            if guard.snapshot()["circuit_state"] != "closed":
                raise AssertionError("successful half-open probe did not close circuit")
            return _result("recovered", request_latency)
        guard.record_failure("probe", RuntimeError("injected probe failure"))
        if guard.snapshot()["circuit_state"] != "open":
            raise AssertionError("failed half-open probe did not reopen circuit")
        return _result("fallback_chunked", request_latency + fallback_latency)
    raise ValueError(f"unknown model circuit variant: {variant}")


def _stream(variant: str, data: dict[str, Any]) -> dict[str, Any]:
    if variant == "native_stream":
        result = _result("streaming", int(data["first_delta_latency_ms"]))
        supported = True
    else:
        failure_latency = int(data.get("failure_latency_ms", 0))
        fallback_latency = int(data.get("fallback_latency_ms", 0))
        if variant not in {"stream_unavailable", "stream_interrupted"}:
            raise ValueError(f"unknown stream variant: {variant}")
        if data.get("fallback_available"):
            repeats = 1 if variant == "stream_interrupted" else 0
            result = _result("fallback_chunked", failure_latency + fallback_latency, repeats)
        else:
            result = _result("unavailable", failure_latency)
        supported = False
    guard = ModelProviderGuard(
        provider_id="fault-injection",
        failure_threshold=3,
        recovery_seconds=30,
    )
    guard.record_stream_capability(supported=supported, reason="synthetic fault")
    guard.record_stream_mode(result["outcome"])
    if guard.snapshot()["stream_modes"][result["outcome"]] != 1:
        raise AssertionError("model stream telemetry did not record injected mode")
    return result


def _supercell(variant: str, data: dict[str, Any]) -> dict[str, Any]:
    if variant == "cooldown_active":
        return _result("cooldown", 0)
    if variant == "retry_then_success":
        failures = int(data["failures_before_success"])
        max_retries = int(data["max_retries"])
        repeats = min(failures, max_retries)
        attempts = repeats + 1
        latency = attempts * int(data["request_latency_ms"])
        latency += sum(data.get("retry_after_ms", [])[:repeats])
        outcome = "recovered" if failures <= max_retries else "cooldown"
        return _result(outcome, latency, repeats)
    raise ValueError(f"unknown Supercell variant: {variant}")


async def _quota_decision(data: dict[str, Any]):
    quota = ProcessQuota(
        max_concurrent=int(data["concurrency_limit"]),
        requests_per_minute=int(data["rate_limit"]),
    )
    for index in range(int(data["requests_in_window"])):
        decision = await quota.try_acquire("rate-client")
        if decision.allowed:
            await quota.release(decision.lease_id)
    for index in range(int(data["active_requests"])):
        await quota.try_acquire(f"concurrent-client-{index}")
    return await quota.try_acquire("rate-client")


def _quota(variant: str, data: dict[str, Any]) -> dict[str, Any]:
    if variant == "window_reset":
        return _result("recovered" if data.get("window_elapsed") else "rate_limited", 1 if data.get("window_elapsed") else 0)
    if variant == "quota_check":
        if int(data["query_chars"]) > int(data["max_query_chars"]):
            return _result("request_rejected", 0)
        decision = asyncio.run(_quota_decision(data))
        outcome = {
            None: "accepted",
            "concurrency": "concurrency_limited",
            "rate_limit": "rate_limited",
        }[decision.reason]
        return _result(outcome, 1 if decision.allowed else 0)
    raise ValueError(f"unknown quota variant: {variant}")


def _snapshot(variant: str, data: dict[str, Any]) -> dict[str, Any]:
    if variant != "snapshot_state":
        raise ValueError(f"unknown snapshot variant: {variant}")
    status = data["snapshot_status"]
    if status in {"missing", "unavailable"}:
        return _result("unavailable", 1)
    if data.get("query_kind") != "rag":
        return _result("structured_ready", 1)
    aligned = data.get("snapshot_id") == data.get("rag_snapshot_id")
    if data.get("rag_status") != "ready" or not aligned:
        return _result("bm25_only", 2)
    return _result("stale_ready" if status == "stale" else "ready", 2)


def _grounding(variant: str, data: dict[str, Any]) -> dict[str, Any]:
    if variant != "grounding_check":
        raise ValueError(f"unknown grounding variant: {variant}")
    evidence_map = data.get("evidence", {})
    evidence = "\n".join(
        f"{doc_id} usage rate {' '.join(numbers)}"
        for doc_id, numbers in evidence_map.items()
    )
    answer = "\n".join(
        f"{claim.get('citation') or ''} usage rate {claim.get('number') or ''}"
        for claim in data.get("claims", [])
    )
    grounding = validate_answer_grounding(answer, evidence, set(evidence_map))
    return _result("accepted" if grounding["passed"] else "rejected", len(data.get("claims", [])) + 1)


SIMULATORS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    "model_circuit": _model_circuit,
    "stream_unavailable_fallback": _stream,
    "supercell_429_retry_cooldown": _supercell,
    "request_quota_rate_limit": _quota,
    "stale_snapshot_rag_alignment": _snapshot,
    "grounding_citation_number": _grounding,
}


def evaluate_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    expected = scenario["expected"]
    errors: dict[str, str] = {}
    try:
        actual = SIMULATORS[scenario["category"]](scenario["variant"], scenario.get("input", {}))
    except Exception as exc:
        actual = {
            "outcome": "runner_error",
            "successful_degradation_or_recovery": False,
            "repeated_external_requests": 0,
            "recovery_or_handling_latency_ms": 0,
        }
        errors["execution"] = f"{type(exc).__name__}: {exc}"
    for field in MEASURED_FIELDS:
        if actual.get(field) != expected.get(field):
            errors[field] = f"expected {expected.get(field)!r}, got {actual.get(field)!r}"
    return {
        "id": scenario["id"],
        "category": scenario["category"],
        "variant": scenario["variant"],
        "injected_fault": scenario.get("input", {}),
        "expected": expected,
        "actual": actual,
        "success": not errors,
        "successful_degradation_or_recovery": bool(actual["successful_degradation_or_recovery"]),
        "repeated_external_requests": int(actual["repeated_external_requests"]),
        "recovery_or_handling_latency_ms": int(actual["recovery_or_handling_latency_ms"]),
        "errors": errors,
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    succeeded = sum(item["success"] for item in results)
    latencies = [item["recovery_or_handling_latency_ms"] for item in results]
    category_counts = Counter(item["category"] for item in results)
    category_successes: dict[str, int] = defaultdict(int)
    for item in results:
        category_successes[item["category"]] += int(item["success"])
    return {
        "total_scenarios": total,
        "passed_scenarios": succeeded,
        "failed_scenarios": total - succeeded,
        "success_rate": round(succeeded / total, 4) if total else 0.0,
        "successful_degradation_or_recovery_rate": round(
            sum(item["successful_degradation_or_recovery"] for item in results) / total, 4
        ) if total else 0.0,
        "total_repeated_external_requests": sum(item["repeated_external_requests"] for item in results),
        "average_recovery_or_handling_latency_ms": round(mean(latencies), 2) if latencies else 0.0,
        "max_recovery_or_handling_latency_ms": max(latencies, default=0),
        "categories": {
            category: {
                "total": category_counts[category],
                "passed": category_successes[category],
                "success_rate": round(category_successes[category] / category_counts[category], 4),
            }
            for category in sorted(category_counts)
        },
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_evaluation(
    *,
    scenarios: list[dict[str, Any]] | None = None,
    report_path: Path | None = None,
) -> dict[str, Any]:
    selected = scenarios if scenarios is not None else load_scenarios()
    results = [evaluate_scenario(item) for item in selected]
    report = {
        "schema_version": 1,
        "evaluation_type": "synthetic_fault_injection",
        "external_requests_enabled": False,
        "disclaimer": "Deterministic local simulation; results are not live production fault observations.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenarios_file": str(SCENARIOS_FILE.relative_to(ROOT)),
        "summary": _summarize(results),
        "results": results,
        "failures": [item for item in results if not item["success"]],
    }
    attach_scorecard(report, source="fault_injection")
    if report_path is not None:
        write_report(report, report_path)
    return report


def default_report_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPORTS_DIR / f"fault-injection-{stamp}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic offline fault-injection evaluation.")
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS_FILE)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)
    report_path = args.report or default_report_path()
    report = run_evaluation(scenarios=load_scenarios(args.scenarios), report_path=report_path)
    summary = report["summary"]
    print("=== Synthetic Fault-Injection Summary ===")
    print("No real external requests were made.")
    for key, value in summary.items():
        if key != "categories":
            print(f"{key}: {value}")
    for item in report["failures"]:
        print(f"FAILED {item['id']}: {item['errors']}")
    print(f"Report: {report_path}")
    return 1 if summary["failed_scenarios"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
