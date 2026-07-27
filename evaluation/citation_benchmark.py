"""Offline citation and numeric-grounding benchmark for snapshot RAG answers."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag_quality import validate_answer_grounding


def _load_cases(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        cases = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise ValueError("citation benchmark cases must be a JSON array or JSONL rows")
    return cases


def build_snapshot_citation_cases(
    documents: list[dict[str, Any]],
    *,
    probes_per_source: int = 3,
) -> list[dict[str, Any]]:
    """Build reproducible validator cases from the active snapshot evidence."""
    if probes_per_source <= 0:
        raise ValueError("probes_per_source must be positive")
    by_source: dict[str, list[dict[str, Any]]] = {}
    for document in documents:
        if not isinstance(document, dict):
            continue
        source_type = str(document.get("source_type") or "").strip()
        doc_id = str(document.get("doc_id") or "").strip()
        text = document.get("text")
        snapshot_id = str(document.get("metadata", {}).get("snapshot_id") or "").strip()
        if source_type and doc_id and isinstance(text, str) and text.strip() and snapshot_id:
            by_source.setdefault(source_type, []).append(document)

    cases: list[dict[str, Any]] = []
    for source_type in sorted(by_source):
        candidates = sorted(by_source[source_type], key=lambda item: str(item["doc_id"]))
        limit = min(probes_per_source, len(candidates))
        if limit == len(candidates):
            selected = candidates
        elif limit == 1:
            selected = [candidates[0]]
        else:
            indices = [round(index * (len(candidates) - 1) / (limit - 1)) for index in range(limit)]
            selected = [candidates[index] for index in indices]
        for index, document in enumerate(selected, start=1):
            doc_id = str(document["doc_id"])
            cases.append(
                {
                    "case_id": f"{source_type}_{index:03d}",
                    "snapshot_id": str(document["metadata"]["snapshot_id"]),
                    "answer": f"{document['text']}\nSource: {doc_id}",
                    "evidence": str(document["text"]),
                    "allowed_doc_ids": [doc_id],
                }
            )
    return cases


def evaluate_citation_cases(
    cases: list[dict[str, Any]],
    *,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Validate every answer without failing fast so all failure evidence is retained."""
    observed_snapshot_ids = {
        str(case.get("snapshot_id", "")).strip()
        for case in cases
        if isinstance(case, dict) and str(case.get("snapshot_id", "")).strip()
    }
    resolved_snapshot_id = snapshot_id or (
        next(iter(observed_snapshot_ids)) if len(observed_snapshot_ids) == 1 else "mixed"
    )
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        case_id = (
            str(case.get("case_id") or f"citation_{index:03d}")
            if isinstance(case, dict)
            else f"citation_{index:03d}"
        )
        case_snapshot_id = (
            str(case.get("snapshot_id", "")).strip() if isinstance(case, dict) else ""
        )
        try:
            if not isinstance(case, dict):
                raise ValueError("case must be an object")
            if snapshot_id and case_snapshot_id and case_snapshot_id != snapshot_id:
                raise ValueError(
                    f"case snapshot_id {case_snapshot_id!r} does not match {snapshot_id!r}"
                )
            answer = case["answer"]
            evidence = case["evidence"]
            allowed_doc_ids = case["allowed_doc_ids"]
            if not isinstance(answer, str) or not isinstance(evidence, str):
                raise TypeError("answer and evidence must be strings")
            if not isinstance(allowed_doc_ids, (list, tuple, set)):
                raise TypeError("allowed_doc_ids must be a list, tuple, or set")
            validation = validate_answer_grounding(answer, evidence, allowed_doc_ids)
            grounded = not (
                validation["unsupported_numeric_facts"]
                or validation["unsupported_numeric_claims"]
            )
            invalid_citation = bool(
                validation["unknown_citations"] or validation["missing_citations"]
            )
            rows.append(
                {
                    "case_id": case_id,
                    "snapshot_id": case_snapshot_id or resolved_snapshot_id,
                    "passed": bool(validation["passed"]),
                    "grounded": grounded,
                    "invalid_citation": invalid_citation,
                    **validation,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "case_id": case_id,
                    "snapshot_id": case_snapshot_id or resolved_snapshot_id,
                    "passed": False,
                    "grounded": False,
                    "invalid_citation": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    case_count = len(rows)
    grounded_count = sum(bool(row["grounded"]) for row in rows)
    invalid_citation_count = sum(bool(row["invalid_citation"]) for row in rows)
    snapshot_consistent = (
        len(observed_snapshot_ids) <= 1
        and resolved_snapshot_id != "mixed"
        and (not snapshot_id or not observed_snapshot_ids or observed_snapshot_ids == {snapshot_id})
    )
    return {
        "schema_version": 1,
        "benchmark": "Snapshot RAG answer grounding and citation benchmark",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": resolved_snapshot_id,
        "snapshot_consistent": snapshot_consistent,
        "case_count": case_count,
        "grounded_count": grounded_count,
        "invalid_citation_count": invalid_citation_count,
        "grounding_rate": grounded_count / case_count if case_count else 0.0,
        "invalid_citation_rate": invalid_citation_count / case_count if case_count else 0.0,
        "passed": bool(
            case_count
            and all(bool(row["passed"]) for row in rows)
            and snapshot_consistent
        ),
        "rows": rows,
    }


def _persist_report(report: dict[str, Any], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(report_path)


def run_citation_benchmark(
    cases_path: Path | str,
    report_path: Path | str,
    *,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    try:
        cases = _load_cases(Path(cases_path))
        report = evaluate_citation_cases(cases, snapshot_id=snapshot_id)
    except Exception as exc:
        report = {
            "schema_version": 1,
            "benchmark": "Snapshot RAG answer grounding and citation benchmark",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_id": snapshot_id or "unknown",
            "snapshot_consistent": False,
            "case_count": 0,
            "grounded_count": 0,
            "invalid_citation_count": 0,
            "grounding_rate": 0.0,
            "invalid_citation_rate": 0.0,
            "passed": False,
            "fatal_error": f"{type(exc).__name__}: {exc}",
            "rows": [],
        }
    report["case_source"] = str(cases_path)
    _persist_report(report, Path(report_path))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cases")
    source.add_argument("--documents")
    parser.add_argument("--report", default="evaluation/reports/citation-benchmark.json")
    parser.add_argument("--snapshot-id")
    parser.add_argument("--probes-per-source", type=int, default=3)
    args = parser.parse_args()
    if args.documents:
        documents = json.loads(Path(args.documents).read_text(encoding="utf-8"))
        if not isinstance(documents, list):
            raise SystemExit("--documents must contain a JSON array")
        cases = build_snapshot_citation_cases(
            documents,
            probes_per_source=args.probes_per_source,
        )
        report = evaluate_citation_cases(cases, snapshot_id=args.snapshot_id)
        report["case_source"] = f"generated from {args.documents}"
        _persist_report(report, Path(args.report))
    else:
        report = run_citation_benchmark(args.cases, args.report, snapshot_id=args.snapshot_id)
    print(
        json.dumps(
            {
                "snapshot_id": report["snapshot_id"],
                "case_count": report["case_count"],
                "grounding_rate": report["grounding_rate"],
                "invalid_citation_rate": report["invalid_citation_rate"],
                "passed": report["passed"],
                "report": args.report,
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
