"""Live local RAG smoke test for a running Clash Royale backend.

This test intentionally calls the configured model provider. It targets a local
backend URL and verifies that an open-ended meta question reaches the RAG skill,
uses model synthesis, and passes numeric/citation grounding.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


DEFAULT_BACKEND_URL = "http://127.0.0.1:8092"
DEFAULT_REPORT = Path("evaluation/reports/live-rag-smoke-latest.json")
DEFAULT_QUESTION = (
    "What are the current mainstream decks in the meta? Use the available evidence."
)


def request_answer(client: httpx.Client, backend_url: str, question: str, dataset_scope: str) -> dict[str, Any]:
    payload = {
        "session_id": f"live-rag-smoke-{uuid4().hex[:8]}",
        "user_id": "live-rag-smoke",
        "dataset_scope": dataset_scope,
        "input": [
            {
                "role": "user",
                "content": [{"type": "text", "text": question}],
            }
        ],
    }
    answer = ""
    events: list[dict[str, Any]] = []
    trace: dict[str, Any] = {}
    with client.stream("POST", f"{backend_url}/process", json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event)
            if event.get("object") == "content":
                answer += str(event.get("text") or "")
            elif event.get("object") == "trace":
                trace = event
    return {"answer": answer, "events": events, "trace": trace}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    backend_url = args.backend_url.rstrip("/")
    with httpx.Client(timeout=args.timeout_seconds, trust_env=False) as client:
        health = client.get(f"{backend_url}/health")
        health.raise_for_status()
        health_payload = health.json()
        response = request_answer(client, backend_url, args.question, args.dataset_scope)

    trace = response["trace"]
    metadata = trace.get("metadata") or {}
    grounding = metadata.get("grounding_validation") or {}
    runtime_attributes = ((trace.get("runtime_events") or {}).get("attributes") or {})
    parsed = trace.get("parsed") or {}
    answer = response["answer"]
    events = response["events"]
    report = {
        "benchmark": "Live local RAG smoke",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backend_url": backend_url,
        "health": {
            "status": health_payload.get("status"),
            "runtime_role": health_payload.get("runtime_role"),
            "model_api_configured": health_payload.get("model_api_configured"),
            "external_api_required": health_payload.get("external_api_required"),
        },
        "question": args.question,
        "dataset_scope": args.dataset_scope,
        "answer_length": len(answer),
        "event_count": len(events),
        "event_objects": sorted({str(event.get("object")) for event in events}),
        "trace": {
            "selected_skill": trace.get("selected_skill"),
            "mode": trace.get("mode"),
            "parsed_intent": parsed.get("intent"),
            "parse_source": parsed.get("parse_source"),
        },
        "metadata": {
            "model_generation": metadata.get("model_generation"),
            "model_stream": metadata.get("model_stream"),
            "snapshot_id": metadata.get("snapshot_id") or runtime_attributes.get("snapshot_id"),
            "grounding_validation": grounding,
        },
    }
    report["passed"] = bool(
        report["answer_length"] > 0
        and report["trace"]["parse_source"] == "llm_parser"
        and report["trace"]["selected_skill"] == "EvidenceSynthesisSkill"
        and report["trace"]["mode"] == "rag_synthesis"
        and report["metadata"]["model_generation"] == "api"
        and grounding.get("passed") is True
        and not grounding.get("unknown_citations")
        and not grounding.get("unsupported_numeric_claims")
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--dataset-scope", default="7d_all")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--timeout-seconds", type=float, default=420.0)
    args = parser.parse_args()

    report = build_report(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "benchmark": report["benchmark"],
                "passed": report["passed"],
                "answer_length": report["answer_length"],
                "event_count": report["event_count"],
                "trace": report["trace"],
                "model_generation": report["metadata"]["model_generation"],
                "grounding_passed": report["metadata"]["grounding_validation"].get("passed"),
            },
            ensure_ascii=False,
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
