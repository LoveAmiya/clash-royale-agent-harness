"""Exercise the real model, Supercell, and RAG paths against a running backend.

This intentionally makes external calls. It is separate from the offline unit
suite so contributors without credentials can still run deterministic tests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import httpx


BACKEND_URL = os.getenv("LIVE_API_BACKEND_URL", "http://127.0.0.1:8091").rstrip("/")
PROCESS_URL = f"{BACKEND_URL}/process"
HEALTH_URL = f"{BACKEND_URL}/health"


def persist_report(report_path: Path | None, report: dict) -> None:
    if report_path is None:
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def request_answer(client: httpx.Client, question: str) -> tuple[str, dict, list[dict]]:
    payload = {
        "session_id": f"live-api-smoke-{uuid.uuid4().hex[:8]}",
        "user_id": "live-api-smoke",
        "input": [{"role": "user", "content": [{"type": "text", "text": question}]}],
    }
    answer = ""
    trace: dict = {}
    events: list[dict] = []
    with client.stream("POST", PROCESS_URL, json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            events.append(event)
            if event.get("object") == "content":
                answer += event.get("text", "")
            elif event.get("object") == "trace":
                trace = event
            elif event.get("object") == "message" and event.get("status") == "completed" and not answer:
                answer = "".join(
                    item.get("text", "")
                    for item in event.get("content", [])
                    if item.get("type") == "text"
                )
    if not trace:
        raise AssertionError("SSE response did not include a trace event")
    return answer, trace, events


def assert_streaming_contract(events: list[dict]) -> None:
    objects = [event.get("object") for event in events]
    trace_index = objects.index("trace")
    parse_index = next(
        index
        for index, event in enumerate(events)
        if event.get("object") == "execution"
        and event.get("step_id") == "parse"
        and event.get("status") == "running"
    )
    content_index = next(index for index, event in enumerate(events) if event.get("object") == "content")
    assert parse_index < trace_index, events
    assert content_index < trace_index, events


def assert_api_metadata(trace: dict) -> None:
    metadata = trace.get("metadata") or {}
    parser_api = metadata.get("parser_api") or {}
    live_data = metadata.get("live_data") or {}
    assert parser_api.get("status") == "api", parser_api
    assert live_data.get("status") == "live_sample", live_data
    assert live_data.get("source") == "supercell_api", live_data
    assert live_data.get("static_card_fallback_count") == 0, live_data


def main(report_path: Path | None = None) -> int:
    report: dict = {}
    with httpx.Client(timeout=300.0) as client:
        health = client.get(HEALTH_URL)
        health.raise_for_status()
        status = health.json()
        assert status.get("status") == "healthy", status
        assert status.get("live_data_enabled") is True, status
        assert status.get("external_api_required") is True, status
        assert status.get("model_api_configured") is True, status

        ranking_answer, ranking_trace, ranking_events = request_answer(client, "当前实时使用率前十的卡牌有哪些？")
        report["ranking"] = {"question": "当前实时使用率前十的卡牌有哪些？", "answer": ranking_answer, "trace": ranking_trace}
        persist_report(report_path, report)
        assert ranking_trace.get("parsed", {}).get("parse_source") == "llm_parser", ranking_trace
        assert ranking_trace.get("selected_skill") == "CardMetaSkill", ranking_trace
        assert ranking_trace.get("mode") == "direct", ranking_trace
        assert_api_metadata(ranking_trace)
        assert "Supercell API live sample" in ranking_answer, ranking_answer
        assert "cards_meta.json" not in ranking_answer, ranking_answer
        assert_streaming_contract(ranking_events)

        question = "雷电巨人的使用率、胜率，还有当前环境主流卡组"
        answer, trace, events = request_answer(client, question)
        report["multi_intent"] = {"question": question, "answer": answer, "trace": trace}
        persist_report(report_path, report)
        assert trace.get("parsed", {}).get("intent") == "multi_intent", trace
        assert trace.get("parsed", {}).get("parse_source") == "llm_parser", trace
        assert trace.get("selected_skill") == "MultiIntentOrchestrator", trace
        assert trace.get("mode") == "mixed", trace
        assert_api_metadata(trace)

        sub_results = trace.get("sub_results") or []
        assert len(sub_results) == 2, sub_results
        direct, rag = sub_results
        assert direct.get("id") == "q1" and direct.get("selected_skill") == "CardMetaSkill", direct
        assert direct.get("mode") == "direct" and direct.get("status") == "success", direct
        assert rag.get("id") == "q2" and rag.get("selected_skill") == "EvidenceSynthesisSkill", rag
        assert rag.get("mode") == "rag_synthesis" and rag.get("status") == "success", rag
        assert (rag.get("metadata") or {}).get("model_generation") == "api", rag
        assert "Supercell API live sample" in answer, answer
        assert "cards_meta.json" not in answer, answer
        assert "top_decks.json" not in answer, answer
        assert_streaming_contract(events)
        rag_stream = (rag.get("metadata") or {}).get("model_stream")
        assert rag_stream in {"streaming", "fallback_chunked", "unavailable"}, rag

        rag_question = "当前环境主流卡组有哪些？"
        rag_answer, rag_trace, rag_events = request_answer(client, rag_question)
        report["rag"] = {"question": rag_question, "answer": rag_answer, "trace": rag_trace}
        persist_report(report_path, report)
        assert rag_trace.get("parsed", {}).get("intent") == "meta_analysis_query", rag_trace
        assert rag_trace.get("parsed", {}).get("parse_source") == "llm_parser", rag_trace
        assert rag_trace.get("selected_skill") == "EvidenceSynthesisSkill", rag_trace
        assert rag_trace.get("mode") == "rag_synthesis", rag_trace
        assert_api_metadata(rag_trace)
        rag_metadata = rag_trace.get("metadata") or {}
        assert rag_metadata.get("model_generation") == "api", rag_metadata
        assert rag_metadata.get("model_stream") in {"streaming", "fallback_chunked"}, rag_metadata
        grounding = rag_metadata.get("grounding_validation") or {}
        assert grounding.get("passed") is True, grounding
        assert not grounding.get("unknown_citations"), grounding
        assert not grounding.get("unsupported_numeric_claims"), grounding
        assert "Supercell API live sample" in rag_answer, rag_answer
        assert_streaming_contract(rag_events)

    persist_report(report_path, report)
    print("LIVE_API_SMOKE_OK")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, help="Write successful live API responses and traces to JSON.")
    args = parser.parse_args()
    try:
        raise SystemExit(main(args.report))
    except (AssertionError, httpx.HTTPError, json.JSONDecodeError) as exc:
        print(f"LIVE_API_SMOKE_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
