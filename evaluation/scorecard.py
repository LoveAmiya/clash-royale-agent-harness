"""Normalize existing evaluation reports into one regression scorecard.

This module is deliberately offline. It reads aggregate report fields, never
calls a provider, and never carries raw questions into the unified output.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from evaluation.metrics import build_scorecard


DIMENSION_KEYS = (
    "snapshot_group_id",
    "snapshot_id",
    "dataset_scope",
    "deck_mode",
    "entity_mode",
    "model",
    "prompt_hash",
)

METRIC_FIELDS = {
    "retrieval_recall": ("retrieval_relevant", "retrieval_expected"),
    "assertion_support_rate": ("assertions_supported", "assertions_total"),
    "citation_precision": ("citations_correct", "citations_total"),
    "refusal_accuracy": ("refusal_correct",),
    "boundary_violation_rate": ("boundary_violations",),
    "first_token_latency_ms": ("first_token_latency_ms",),
    "total_latency_ms": ("total_latency_ms",),
    "timeout_rate": ("timed_out",),
    "fallback_rate": ("fallback_used",),
    "token_count": ("token_count",),
    "estimated_cost": ("estimated_cost",),
}


def _citation_counts(rows: Iterable[dict[str, Any]]) -> tuple[int, int]:
    correct = 0
    total = 0
    for row in rows:
        cited = row.get("cited_doc_ids") or []
        unknown = row.get("unknown_citations") or []
        cited_count = len(cited) if isinstance(cited, list) else 0
        unknown_count = len(unknown) if isinstance(unknown, list) else 0
        total += cited_count + unknown_count
        correct += cited_count
    return correct, total


def normalize_report(report: dict[str, Any], *, source: str = "report") -> list[dict[str, Any]]:
    """Map one legacy report shape to metric rows without retaining raw text."""
    benchmark = str(report.get("benchmark") or "").lower()
    rows: list[dict[str, Any]] = []

    methods = report.get("methods")
    if isinstance(methods, dict) and isinstance(methods.get("hybrid_rerank"), dict):
        metrics = methods["hybrid_rerank"].get("metrics") or {}
        rows.append(
            {
                "source": source,
                "retrieval_relevant": int(metrics.get("hits_at_k") or 0),
                "retrieval_expected": int(metrics.get("case_count") or 0),
            }
        )
        return rows

    if "citation" in benchmark and isinstance(report.get("rows"), list):
        citation_rows = [row for row in report["rows"] if isinstance(row, dict)]
        correct, total = _citation_counts(citation_rows)
        rows.append(
            {
                "source": source,
                "assertions_supported": int(report.get("grounded_count") or 0),
                "assertions_total": int(report.get("case_count") or len(citation_rows)),
                "citations_correct": correct,
                "citations_total": total,
            }
        )
        return rows

    results = report.get("results")
    if not isinstance(results, list):
        return rows

    if report.get("evaluation_type") == "synthetic_fault_injection":
        for item in results:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "source": source,
                    "boundary_violations": 0 if item.get("success") else 1,
                    "total_latency_ms": float(item.get("recovery_or_handling_latency_ms") or 0),
                }
            )
        return rows

    if "live structured-query parser" in benchmark:
        for item in results:
            if not isinstance(item, dict):
                continue
            row: dict[str, Any] = {
                "source": source,
                "total_latency_ms": float(item.get("elapsed_seconds") or 0) * 1000,
            }
            if item.get("expected_intent") == "reject":
                row["refusal_correct"] = bool(item.get("success"))
            rows.append(row)
        return rows

    # Deterministic routing reports contribute refusal and explicit boundary
    # assertions only. Ordinary case failures are not mislabeled as grounding
    # or retrieval failures.
    for item in results:
        if not isinstance(item, dict) or item.get("skipped"):
            continue
        row = {"source": source}
        for field in (
            "first_token_latency_ms",
            "total_latency_ms",
            "timed_out",
            "fallback_used",
        ):
            if field in item and item.get(field) is not None:
                row[field] = item[field]
        if item.get("expected_intent") == "reject":
            row["refusal_correct"] = bool(item.get("success"))
        errors = item.get("errors") or {}
        category = str(item.get("category") or "").lower()
        if "boundary" in category or any("boundary" in str(key).lower() for key in errors):
            row["boundary_violations"] = 0 if item.get("success") else 1
        if len(row) > 1:
            rows.append(row)
    return rows


def _resolved_dimensions(
    reports: list[dict[str, Any]],
    explicit: dict[str, Any] | None,
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key in DIMENSION_KEYS:
        values = {
            str(report[key])
            for report in reports
            if report.get(key) not in (None, "")
        }
        resolved[key] = next(iter(values)) if len(values) == 1 else ("mixed" if values else "unknown")
    for key, value in (explicit or {}).items():
        if key in DIMENSION_KEYS and value not in (None, ""):
            resolved[key] = value
    return resolved


def build_unified_scorecard(
    reports: list[dict[str, Any]],
    *,
    dimensions: dict[str, Any] | None = None,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    source_names = sources or [str(report.get("benchmark") or "report") for report in reports]
    metric_rows: list[dict[str, Any]] = []
    for index, report in enumerate(reports):
        source = source_names[index] if index < len(source_names) else f"report-{index + 1}"
        metric_rows.extend(normalize_report(report, source=source))
    scorecard = build_scorecard(
        metric_rows,
        dimensions=_resolved_dimensions(reports, dimensions),
    )
    scorecard["schema_version"] = 1
    scorecard["generated_at"] = datetime.now(timezone.utc).isoformat()
    scorecard["sources"] = list(source_names)
    scorecard["metric_coverage"] = {
        metric: sum(all(field in row for field in fields) for row in metric_rows)
        for metric, fields in METRIC_FIELDS.items()
    }
    return scorecard


def attach_scorecard(
    report: dict[str, Any],
    *,
    dimensions: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Attach the unified view while preserving every legacy report field."""
    report["scorecard"] = build_unified_scorecard(
        [report],
        dimensions=dimensions,
        sources=[source or str(report.get("benchmark") or report.get("evaluation_type") or "report")],
    )
    return report


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build one local scorecard from existing evaluation reports.")
    parser.add_argument("--report", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    for key in DIMENSION_KEYS:
        parser.add_argument(f"--{key.replace('_', '-')}", dest=key)
    args = parser.parse_args(argv)
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.report]
    dimensions = {key: getattr(args, key) for key in DIMENSION_KEYS if getattr(args, key)}
    scorecard = build_unified_scorecard(
        reports,
        dimensions=dimensions,
        sources=[str(path) for path in args.report],
    )
    _write_json_atomic(args.output, scorecard)
    print(json.dumps({"output": str(args.output), **scorecard}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
